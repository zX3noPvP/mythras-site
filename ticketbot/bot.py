"""Mythras ticket bot: a panel with buttons in #tickets opens a private channel in the BILLING & SALES category;
when the ticket is closed the conversation is saved as a page on the website and linked in #transcripts.

    .venv/bin/python ticketbot/bot.py

Settings come from the environment (/etc/mythras-site.env on the VPS, .env locally), see ticketbot/README.md.
Channels and roles can be given as IDs or as names.
"""

import asyncio
import datetime as dt
import json
import logging
import os
import secrets
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import tickets  # noqa: E402

log = logging.getLogger("mythras.tickets")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.getenv("TICKETS_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("TICKETS_GUILD_ID", "0") or 0)
CATEGORY = os.getenv("TICKETS_CATEGORY", "💰 BILLING & SALES")
TRANSCRIPTS = os.getenv("TICKETS_TRANSCRIPTS_CHANNEL", "📑┃transcripts")
SITE_URL = os.getenv("SITE_URL", "https://www.mythras.eu").rstrip("/")
INACTIVE_HOURS = int(os.getenv("TICKETS_INACTIVE_HOURS", "48") or 0)  # 0 = never closed for inactivity
MAX_FILE = 8 * 1024 * 1024        # attachments bigger than this keep only their name in the transcript
MAX_FILES_TOTAL = 50 * 1024 * 1024
MAX_MESSAGES = 5000

RED, GREEN, TEAL, GREY = 0xE5243B, 0x3BA55C, 0x1ABC9C, 0x5C5C5C

# questions asked before the channel opens: (label, placeholder, long text, required)
FORMS = {
    "general": [
        ("Your question", "Write it in as much detail as you can", True, True),
    ],
    "billing": [
        ("What is it about?", "Buy Pro, Anti-XRay, Anti-ESP, renewal, payment, refund, invoice...", False, True),
        ("Details", "Which key or server, what you need, what went wrong", True, True),
        ("PayPal transaction ID or order number", "Only for a payment already made", False, False),
    ],
}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def db():
    return tickets.connect()


# ---------- lookups ----------

def find_channel(guild, wanted, kind=None):
    for ch in guild.channels:
        if (str(ch.id) == wanted or ch.name == wanted) and (kind is None or isinstance(ch, kind)):
            return ch
    return None


def kind_roles(guild, kind):
    wanted = tickets.staff_roles(kind)
    return [r for r in guild.roles if tickets.matches({"id": r.id, "name": r.name}, wanted)]


def is_staff(member, kind=None):
    """Administrator, or a role that handles this kind of ticket (any kind when kind is None)."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    kinds = [kind] if kind else list(tickets.KINDS)
    return any(r in member.roles for k in kinds for r in kind_roles(member.guild, k))


def ticket_for_channel(channel_id):
    with db() as con:
        return con.execute("SELECT * FROM tickets WHERE channel_id = ? AND status IN ('open', 'closing')",
                           (str(channel_id),)).fetchone()


def embed(title, description="", color=TEAL):
    return discord.Embed(title=title, description=description, color=color)


def transcript_url(token):
    return f"{SITE_URL}/transcripts/{token}"


# ---------- the panel in #tickets ----------

def panel_embed():
    e = embed("Welcome to the Mythras Support & Sales Center!", (
        "Questions about Mythras, a purchase or a payment? Our team is ready to help.\n\n"
        "**How it works**\n"
        "1️⃣ Pick the button that fits your request\n"
        "2️⃣ A private channel opens, visible only to you and our team\n"
        "3️⃣ Describe your request and we'll reply right there\n\n"
        "**❓ General Questions** · anything about Mythras that you don't want to ask in public\n"
        "**💳 Billing & Sales** · buy Pro or an add-on, renewals, payments, refunds, invoices\n\n"
        "**Get a faster answer**\n"
        "For payments have your **PayPal transaction ID** ready. No need to send your license key: "
        "we find it from your Discord account.\n\n"
        "🇮🇹 Puoi scrivere anche in italiano."), RED)
    e.set_image(url=f"{SITE_URL}/static/og.png")
    e.set_footer(text="© Mythras AntiCheat · Staff never asks for your password")
    return e


class PanelView(discord.ui.View):
    """Persistent: the buttons keep working after a restart (fixed custom_id)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Questions", emoji="❓", style=discord.ButtonStyle.primary,
                       custom_id="mythras:ticket:open:general")
    async def general(self, interaction, _button):
        await ask_form(interaction, "general")

    @discord.ui.button(label="Billing & Sales", emoji="💳", style=discord.ButtonStyle.success,
                       custom_id="mythras:ticket:open:billing")
    async def billing(self, interaction, _button):
        await ask_form(interaction, "billing")


class OpenForm(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title=f"{tickets.KINDS[kind]['label']} ticket", timeout=600)
        self.kind = kind
        self.inputs = []
        for label, placeholder, long, required in FORMS[kind]:
            field = discord.ui.TextInput(label=label, placeholder=placeholder, required=required,
                                         style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
                                         max_length=1500 if long else 200)
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction):
        answers = [[f.label, f.value.strip()] for f in self.inputs if f.value.strip()]
        await open_ticket(interaction, self.kind, answers)


busy = set()        # members whose ticket is being created right now (double clicks)
cooldown = {}       # member id -> when they last opened a ticket


async def ask_form(interaction, kind):
    member = interaction.user
    existing = open_ticket_of(interaction.guild, member.id)
    if existing:
        await interaction.response.send_message(
            f"You already have an open ticket: <#{existing}>. Write there, or close it first.", ephemeral=True)
        return
    last = cooldown.get(member.id)
    if last and (dt.datetime.now() - last).total_seconds() < 60 and not is_staff(member):
        await interaction.response.send_message("Wait a minute before opening another ticket.", ephemeral=True)
        return
    await interaction.response.send_modal(OpenForm(kind))


def open_ticket_of(guild, member_id):
    """Channel ID of the member's open ticket; tickets whose channel was deleted by hand are closed."""
    with db() as con:
        for row in con.execute("SELECT * FROM tickets WHERE opener_id = ? AND status IN ('open', 'closing')",
                               (str(member_id),)).fetchall():
            if guild.get_channel(int(row["channel_id"] or 0)) is None:
                con.execute("UPDATE tickets SET status = 'closed', closed_at = ?, close_reason = ? WHERE id = ?",
                            (now_iso(), "Channel deleted by hand", row["id"]))
                continue
            return row["channel_id"]
    return None


async def open_ticket(interaction, kind, answers):
    guild, member = interaction.guild, interaction.user
    if member.id in busy:
        await interaction.response.send_message("Your ticket is being created…", ephemeral=True)
        return
    busy.add(member.id)
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        existing = open_ticket_of(guild, member.id)
        if existing:
            await interaction.followup.send(f"You already have an open ticket: <#{existing}>.", ephemeral=True)
            return
        category = find_channel(guild, CATEGORY, discord.CategoryChannel)
        if category is None:
            log.error("category %r not found: set TICKETS_CATEGORY", CATEGORY)
            await interaction.followup.send("Tickets are not set up yet, please tell the staff.", ephemeral=True)
            return
        with db() as con:
            number = con.execute(
                "INSERT INTO tickets (kind, status, opener_id, opener_name, answers, opened_at) VALUES (?, 'opening', ?, ?, ?, ?)",
                (kind, str(member.id), member.display_name, json.dumps(answers), now_iso())).lastrowid

        roles = kind_roles(guild, kind)
        talk = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True,
                    embed_links=True, add_reactions=True, use_external_emojis=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(**talk),
            guild.me: discord.PermissionOverwrite(**talk, manage_channels=True, manage_permissions=True,
                                                  manage_messages=True),
        }
        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(**talk, manage_messages=True)
        info = tickets.KINDS[kind]
        slug = "".join(c for c in member.name.lower() if c.isalnum())[:20] or "user"
        try:
            channel = await guild.create_text_channel(
                f"{info['prefix']}-{number:04d}-{slug}", category=category, overwrites=overwrites,
                topic=f"Ticket #{number} · {info['label']} · opened by {member} ({member.id})",
                reason=f"Ticket #{number} opened by {member}")
        except discord.HTTPException:
            with db() as con:
                con.execute("DELETE FROM tickets WHERE id = ?", (number,))
            raise
        with db() as con:
            con.execute("UPDATE tickets SET status = 'open', channel_id = ? WHERE id = ?", (str(channel.id), number))
        cooldown[member.id] = dt.datetime.now()

        e = embed(f"{info['emoji']} {info['label']} · ticket #{number}", (
            f"Hi {member.mention}, thanks for reaching out! Our team will answer here as soon as possible.\n"
            "Add screenshots or logs (https://mclo.gs) if they help.\n\n"
            "🔒 **Close** the ticket when you're done."), TEAL)
        for question, answer in answers:
            e.add_field(name=question, value=answer[:1024], inline=False)
        e.set_footer(text=f"Opened by {member} · {member.id}")
        pings = " ".join(r.mention for r in roles)
        await channel.send(content=f"{member.mention} {pings}".strip(), embed=e, view=TicketView(),
                           allowed_mentions=discord.AllowedMentions(users=[member], roles=roles, everyone=False))
        await interaction.followup.send(f"Your ticket is open: {channel.mention}", ephemeral=True)
        log.info("ticket #%s (%s) opened by %s (%s)", number, kind, member, member.id)
    except discord.HTTPException:
        log.exception("could not open a ticket for %s", member.id)
        await interaction.followup.send("Something went wrong, please try again in a minute.", ephemeral=True)
    finally:
        busy.discard(member.id)


# ---------- inside a ticket ----------

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.secondary,
                       custom_id="mythras:ticket:claim")
    async def claim(self, interaction, _button):
        row = ticket_for_channel(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
            return
        if not is_staff(interaction.user, row["kind"]):
            await interaction.response.send_message("Only staff can claim a ticket.", ephemeral=True)
            return
        if row["claimed_by"]:
            await interaction.response.send_message(f"Already claimed by <@{row['claimed_by']}>.", ephemeral=True)
            return
        with db() as con:
            con.execute("UPDATE tickets SET claimed_by = ?, claimed_name = ? WHERE id = ?",
                        (str(interaction.user.id), interaction.user.display_name, row["id"]))
        await interaction.response.send_message(
            embed=embed("", f"🙋 {interaction.user.mention} will handle this ticket.", GREEN),
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger,
                       custom_id="mythras:ticket:close")
    async def close(self, interaction, _button):
        row = ticket_for_channel(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
            return
        if not (is_staff(interaction.user, row["kind"]) or str(interaction.user.id) == row["opener_id"]):
            await interaction.response.send_message("Only staff or who opened the ticket can close it.", ephemeral=True)
            return
        await interaction.response.send_modal(CloseForm())


class CloseForm(discord.ui.Modal, title="Close the ticket"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=300,
                                  placeholder="e.g. Pro key delivered, question answered")

    async def on_submit(self, interaction):
        await interaction.response.defer()
        await close_ticket(interaction.channel, interaction.user, self.reason.value.strip() or None)


async def close_ticket(channel, closer, reason):
    """Locks the channel, saves the transcript, posts the link in #transcripts, DMs the opener, deletes the channel."""
    with db() as con:
        cur = con.execute("UPDATE tickets SET status = 'closing' WHERE channel_id = ? AND status = 'open'",
                          (str(channel.id),))
        if cur.rowcount == 0:
            return  # closed already, or being closed
        row = con.execute("SELECT * FROM tickets WHERE channel_id = ?", (str(channel.id),)).fetchone()
    guild = channel.guild
    opener = guild.get_member(int(row["opener_id"]))
    if opener is None:
        try:
            opener = await guild.fetch_member(int(row["opener_id"]))
        except discord.HTTPException:
            opener = None  # left the server
    try:
        await channel.send(embed=embed("", f"🔒 Ticket closed by {closer.mention}. Saving the transcript…", RED),
                           allowed_mentions=discord.AllowedMentions.none())
        if opener:
            await channel.set_permissions(opener, view_channel=True, read_message_history=True, send_messages=False,
                                          reason="Ticket closed")
        token = secrets.token_urlsafe(24)
        messages = await collect(channel, token)
        with db() as con:
            con.execute(
                "UPDATE tickets SET status = 'closed', closed_by = ?, closed_name = ?, close_reason = ?, closed_at = ?, "
                "token = ?, messages = ? WHERE id = ?",
                (str(closer.id), closer.display_name, reason, now_iso(), token, json.dumps(messages), row["id"]))
    except Exception:
        log.exception("could not close ticket #%s", row["id"])
        with db() as con:
            con.execute("UPDATE tickets SET status = 'open' WHERE id = ?", (row["id"],))
        await channel.send(embed=embed("", "⚠️ The transcript could not be saved, the ticket stays open. "
                                           "Try again, or tell an admin.", RED))
        return

    info = tickets.KINDS[row["kind"]]
    link = discord.ui.View()
    link.add_item(discord.ui.Button(label="Open transcript", emoji="📑", url=transcript_url(token)))
    e = embed(f"{info['emoji']} Ticket #{row['id']} · {info['label']}", "", GREY)
    e.add_field(name="Opened by", value=f"<@{row['opener_id']}>\n{row['opener_name']}")
    e.add_field(name="Claimed by", value=f"<@{row['claimed_by']}>" if row["claimed_by"] else "-")
    e.add_field(name="Closed by", value=closer.mention)
    e.add_field(name="Opened", value=discord.utils.format_dt(dt.datetime.fromisoformat(row["opened_at"]), "f"))
    e.add_field(name="Messages", value=str(sum(1 for m in messages if not m["bot"])))
    if reason:
        e.add_field(name="Reason", value=reason[:1024], inline=False)
    target = find_channel(guild, TRANSCRIPTS, discord.TextChannel)
    if target:
        await target.send(embed=e, view=link, allowed_mentions=discord.AllowedMentions.none())
    else:
        log.error("transcripts channel %r not found: set TICKETS_TRANSCRIPTS_CHANNEL", TRANSCRIPTS)
    if opener:
        try:
            dm = embed(f"Your ticket #{row['id']} on Mythras was closed",
                       (f"Reason: {reason}\n\n" if reason else "") +
                       "You can read the conversation again on our website (log in with Discord).", TEAL)
            await opener.send(embed=dm, view=link)
        except discord.HTTPException:
            pass  # DMs closed
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket #{row['id']} closed by {closer}")
    except discord.HTTPException:
        log.warning("could not delete the channel of ticket #%s", row["id"])
    log.info("ticket #%s closed by %s", row["id"], closer)


async def collect(channel, token):
    """Every message of the channel, oldest first, with attachments copied to data/transcripts/<token>/."""
    out, total, n = [], 0, 0
    folder = tickets.files_dir() / token
    async for m in channel.history(limit=MAX_MESSAGES, oldest_first=True):
        files = []
        for a in m.attachments:
            item = {"name": a.filename, "size": a.size, "type": (a.content_type or "").split(";")[0]}
            if a.size <= MAX_FILE and total + a.size <= MAX_FILES_TOTAL:
                n += 1
                stored = tickets.safe_filename(f"{n}-{a.filename}")
                try:
                    data = await a.read()
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / stored).write_bytes(data)
                    item["stored"] = stored
                    total += len(data)
                except (discord.HTTPException, OSError):
                    log.warning("attachment %s of ticket channel %s not saved", a.filename, channel.id)
            files.append(item)
        out.append({
            "id": str(m.id),
            "author": m.author.display_name,
            "author_id": str(m.author.id),
            "avatar": m.author.display_avatar.replace(size=64, format="png").url,
            "bot": m.author.bot,
            "staff": is_staff(m.author),
            "at": m.created_at.isoformat(timespec="seconds"),
            "edited": m.edited_at is not None,
            "content": m.clean_content,
            "embeds": [{"title": e.title or "", "description": e.description or "",
                        "fields": [[f.name, f.value] for f in e.fields]} for e in m.embeds],
            "files": files,
        })
    return out


# ---------- inactivity ----------

@tasks.loop(minutes=30)
async def inactivity():
    """Waiting for the member's answer for TICKETS_INACTIVE_HOURS: reminder at half time, then closed."""
    guild = client.get_guild(GUILD_ID)
    if not guild or INACTIVE_HOURS <= 0:
        return
    with db() as con:
        rows = con.execute("SELECT * FROM tickets WHERE status = 'open'").fetchall()
    for row in rows:
        channel = guild.get_channel(int(row["channel_id"]))
        if channel is None:
            open_ticket_of(guild, row["opener_id"])  # marks it closed
            continue
        last = None
        async for m in channel.history(limit=30):
            if not m.author.bot:
                last = m
                break
        # only when the staff answered last: a ticket the staff hasn't answered yet is never closed
        if last is None or str(last.author.id) == row["opener_id"] or not is_staff(last.author):
            if row["warned_at"]:
                with db() as con:
                    con.execute("UPDATE tickets SET warned_at = NULL WHERE id = ?", (row["id"],))
            continue
        hours = (discord.utils.utcnow() - last.created_at).total_seconds() / 3600
        if hours >= INACTIVE_HOURS and row["warned_at"]:
            await close_ticket(channel, guild.me, f"No answer for {INACTIVE_HOURS} hours")
        elif hours >= INACTIVE_HOURS / 2 and not row["warned_at"]:
            with db() as con:
                con.execute("UPDATE tickets SET warned_at = ? WHERE id = ?", (now_iso(), row["id"]))
            await channel.send(
                f"<@{row['opener_id']}> do you still need help? This ticket closes by itself in "
                f"{INACTIVE_HOURS // 2} hours without an answer.",
                allowed_mentions=discord.AllowedMentions(users=True))


# ---------- slash commands ----------

class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # transcripts: enable "Message Content Intent" on the Bot page
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(PanelView())
        self.add_view(TicketView())
        guild = discord.Object(id=GUILD_ID)
        self.tree.add_command(ticketpanel, guild=guild)
        self.tree.add_command(ticket_group, guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.error("the bot is not in the server %s (TICKETS_GUILD_ID)", GUILD_ID)
            return
        for what, found in (("category", find_channel(guild, CATEGORY, discord.CategoryChannel)),
                            ("transcripts channel", find_channel(guild, TRANSCRIPTS, discord.TextChannel))):
            log.info("%s: %s", what, f"#{found.name} ({found.id})" if found else "NOT FOUND")
        for kind in tickets.KINDS:
            log.info("%s staff: %s", kind, ", ".join(r.name for r in kind_roles(guild, kind)) or "only administrators")
        if not inactivity.is_running():
            inactivity.start()
        log.info("ready as %s", self.user)


client = Bot()


@app_commands.command(name="ticketpanel", description="Post the ticket panel in this channel")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def ticketpanel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only administrators.", ephemeral=True)
        return
    await interaction.channel.send(embed=panel_embed(), view=PanelView())
    await interaction.response.send_message("Panel posted.", ephemeral=True)


ticket_group = app_commands.Group(name="ticket", description="Manage the ticket of this channel", guild_only=True)


async def staff_in_ticket(interaction):
    row = ticket_for_channel(interaction.channel_id)
    if row is None:
        await interaction.response.send_message("Use it inside an open ticket.", ephemeral=True)
        return None
    if not is_staff(interaction.user, row["kind"]):
        await interaction.response.send_message("Only staff.", ephemeral=True)
        return None
    return row


@ticket_group.command(name="add", description="Let a member see and write in this ticket")
async def ticket_add(interaction: discord.Interaction, member: discord.Member):
    if await staff_in_ticket(interaction) is None:
        return
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True,
                                              attach_files=True, embed_links=True, reason=f"Added by {interaction.user}")
    await interaction.response.send_message(embed=embed("", f"➕ {member.mention} added by {interaction.user.mention}.",
                                                        GREEN), allowed_mentions=discord.AllowedMentions.none())


@ticket_group.command(name="remove", description="Remove a member from this ticket")
async def ticket_remove(interaction: discord.Interaction, member: discord.Member):
    row = await staff_in_ticket(interaction)
    if row is None:
        return
    if str(member.id) == row["opener_id"]:
        await interaction.response.send_message("That's who opened the ticket: close it instead.", ephemeral=True)
        return
    await interaction.channel.set_permissions(member, overwrite=None, reason=f"Removed by {interaction.user}")
    await interaction.response.send_message(embed=embed("", f"➖ {member.mention} removed.", GREY),
                                            allowed_mentions=discord.AllowedMentions.none())


@ticket_group.command(name="close", description="Close this ticket and save the transcript")
@app_commands.describe(reason="Shown in #transcripts and sent to the member")
async def ticket_close(interaction: discord.Interaction, reason: str = None):
    row = ticket_for_channel(interaction.channel_id)
    if row is None:
        await interaction.response.send_message("Use it inside an open ticket.", ephemeral=True)
        return
    if not (is_staff(interaction.user, row["kind"]) or str(interaction.user.id) == row["opener_id"]):
        await interaction.response.send_message("Only staff or who opened the ticket.", ephemeral=True)
        return
    await interaction.response.send_message("Closing…", ephemeral=True)
    await close_ticket(interaction.channel, interaction.user, reason)


def main():
    if not TOKEN or not GUILD_ID:
        sys.exit("Set TICKETS_BOT_TOKEN and TICKETS_GUILD_ID (see ticketbot/README.md).")
    with tickets.connect() as con:  # creates the table; a ticket half created when the bot stopped is dropped
        con.execute("DELETE FROM tickets WHERE status = 'opening'")
        con.execute("UPDATE tickets SET status = 'open' WHERE status = 'closing'")
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
