#!/usr/bin/env python3
"""
Builds the Mythras Discord server in one go: roles, categories, channels, permissions, welcome/rules/FAQ
messages and server settings. At the end it prints the IDs for license.properties
(discord.guild-id, discord.staff-role-id, discord.role.pro, discord.log-channel-id).

Only the standard library is needed. It talks to the Discord REST API with the bot token.

    1. https://discord.com/developers/applications -> your application -> Bot -> Reset Token (copy it)
    2. Invite the bot with Administrator (only for the setup, you can lower it afterwards):
       https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot%20applications.commands&permissions=8
    3. DISCORD_BOT_TOKEN=... python tools/discord_setup.py <server id>            (dry run: shows what it would do)
       DISCORD_BOT_TOKEN=... python tools/discord_setup.py <server id> --apply    (does it)
       add --wipe on a brand new server to delete the default channels first (asks to type the server name)

Running it again is safe: roles and channels are found by name and updated, nothing is created twice,
messages are posted only in channels that are still empty. Edit ROLES / LAYOUT / MESSAGES below to change it.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
SITE = "https://mythras.eu"

# ---- permission bits (https://discord.com/developers/docs/topics/permissions) ----
CREATE_INVITE = 1 << 0
KICK = 1 << 1
BAN = 1 << 2
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_GUILD = 1 << 5
ADD_REACTIONS = 1 << 6
VIEW_AUDIT_LOG = 1 << 7
PRIORITY_SPEAKER = 1 << 8
STREAM = 1 << 9
VIEW = 1 << 10
SEND = 1 << 11
MANAGE_MESSAGES = 1 << 13
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
HISTORY = 1 << 16
MENTION_EVERYONE = 1 << 17
EXTERNAL_EMOJIS = 1 << 18
CONNECT = 1 << 20
SPEAK = 1 << 21
MUTE_MEMBERS = 1 << 22
DEAFEN_MEMBERS = 1 << 23
MOVE_MEMBERS = 1 << 24
USE_VAD = 1 << 25
CHANGE_NICKNAME = 1 << 26
MANAGE_NICKNAMES = 1 << 27
MANAGE_ROLES = 1 << 28
MANAGE_WEBHOOKS = 1 << 29
MANAGE_EXPRESSIONS = 1 << 30
APP_COMMANDS = 1 << 31
MANAGE_THREADS = 1 << 34
PUBLIC_THREADS = 1 << 35
PRIVATE_THREADS = 1 << 36
EXTERNAL_STICKERS = 1 << 37
SEND_IN_THREADS = 1 << 38
MODERATE_MEMBERS = 1 << 40
VOICE_MESSAGES = 1 << 46
SEND_POLLS = 1 << 49

CHAT = SEND | SEND_IN_THREADS | ADD_REACTIONS | EMBED_LINKS | ATTACH_FILES | EXTERNAL_EMOJIS | EXTERNAL_STICKERS
READ = VIEW | HISTORY
VOICE = VIEW | CONNECT | SPEAK | STREAM | USE_VAD
MODERATION = (KICK | BAN | MODERATE_MEMBERS | MANAGE_MESSAGES | MANAGE_THREADS | MANAGE_NICKNAMES
              | VIEW_AUDIT_LOG | MUTE_MEMBERS | DEAFEN_MEMBERS | MOVE_MEMBERS | PRIORITY_SPEAKER)

# what a normal member may do anywhere (channels below take away or add on top of this)
EVERYONE = (READ | CHAT | CREATE_INVITE | CHANGE_NICKNAME | APP_COMMANDS | PUBLIC_THREADS | CONNECT | SPEAK
            | STREAM | USE_VAD | VOICE_MESSAGES | SEND_POLLS)

# ---- roles, top to bottom. "key" is how the layout refers to them ----
ROLES = [
    dict(key="founder", name="👑 Founder", color=0xE5243B, hoist=True, perms=ADMINISTRATOR),
    dict(key="admin", name="🛡️ Admin", color=0xF04747, hoist=True, perms=ADMINISTRATOR),
    dict(key="dev", name="🔧 Developer", color=0x9B59B6, hoist=True,
         perms=EVERYONE | MODERATION | MANAGE_CHANNELS | MANAGE_WEBHOOKS | MENTION_EVERYONE),
    dict(key="staff", name="🎫 Support", color=0x3498DB, hoist=True, perms=EVERYONE | MODERATION),
    dict(key="bots", name="🤖 Bots", color=0x5C5C5C, hoist=False, perms=EVERYONE | MANAGE_MESSAGES | EMBED_LINKS),
    dict(key="pro", name="💎 Pro", color=0x1ABC9C, hoist=True, perms=EVERYONE),
    dict(key="partner", name="🤝 Partner", color=0xF1C40F, hoist=False, perms=EVERYONE),
    dict(key="updates", name="🔔 Updates", color=0, hoist=False, perms=0, mentionable=True),
]
STAFF_KEYS = ("dev", "staff", "bots")  # admin/founder have Administrator, they see everything anyway

# ---- channel kinds ----
TEXT, VOICE_CH, CATEGORY, NEWS, FORUM = 0, 2, 4, 5, 15

# access presets: role key -> (allow, deny). "@everyone" is the default role
def readonly():
    """Everyone reads, only staff writes."""
    return {"@everyone": (READ, SEND | SEND_IN_THREADS | PUBLIC_THREADS | ADD_REACTIONS),
            "staff": (SEND | MANAGE_MESSAGES, 0), "dev": (SEND | MANAGE_MESSAGES, 0), "bots": (SEND | EMBED_LINKS, 0)}

def open_chat():
    return {"@everyone": (READ | CHAT, 0)}

def pro_only():
    return {"@everyone": (0, VIEW | CONNECT), "pro": (READ | CHAT | VOICE, 0), "partner": (READ | CHAT | VOICE, 0),
            "staff": (READ | CHAT | VOICE | MANAGE_MESSAGES, 0), "dev": (READ | CHAT | VOICE | MANAGE_MESSAGES, 0),
            "bots": (READ | SEND, 0)}

def staff_only():
    return {"@everyone": (0, VIEW | CONNECT), "staff": (READ | CHAT | VOICE, 0), "dev": (READ | CHAT | VOICE, 0),
            "bots": (READ | SEND | EMBED_LINKS, 0)}

def log_channel():
    """Staff read, only bots write (the license server posts key events here)."""
    return {"@everyone": (0, VIEW), "staff": (READ, SEND), "dev": (READ, SEND), "bots": (READ | SEND | EMBED_LINKS, 0)}

def ticket_category():
    """Hidden from members: the ticket bot creates one channel per ticket here and adds the member to it."""
    return {"@everyone": (0, VIEW),
            "staff": (READ | CHAT | MANAGE_MESSAGES, 0), "dev": (READ | CHAT | MANAGE_MESSAGES, 0),
            "bots": (READ | CHAT | MANAGE_CHANNELS | MANAGE_ROLES | MANAGE_MESSAGES | MANAGE_THREADS, 0)}

# ---- layout: category -> channels. A channel may set "id_for" to print its ID for license.properties ----
LAYOUT = [
    dict(name="📌 INFORMATION", perms=readonly(), channels=[
        dict(key="welcome", name="👋┃welcome", topic="Welcome to Mythras AntiCheat"),
        dict(key="rules", name="📜┃rules", topic="Read before writing · Leggi prima di scrivere"),
        dict(key="announcements", name="📢┃announcements", kind=NEWS, topic="News about Mythras"),
        dict(key="changelog", name="📦┃changelog", kind=NEWS, topic=f"Every release · {SITE}/changelog"),
        dict(key="links", name="🔗┃links", topic=f"Website, downloads and docs · {SITE}"),
    ]),
    dict(name="🛒 SHOP", perms=readonly(), channels=[
        dict(key="plans", name="💳┃plans-and-prices", topic=f"Free, Pro and add-ons · {SITE}"),
        dict(key="addons", name="🧩┃add-ons", topic="Anti-XRay and Anti-ESP: one payment, for ever"),
    ]),
    dict(name="💬 COMMUNITY", perms=open_chat(), channels=[
        dict(key="general", name="💬┃general", topic="English chat", slowmode=3),
        dict(key="italiano", name="🇮🇹┃italiano", topic="Chat in italiano", slowmode=3),
        dict(key="showcase", name="📸┃showcase", topic="Clips of Mythras catching cheaters"),
        dict(key="suggestions", name="💡┃suggestions", kind=FORUM, topic="One post per idea. Vote with a reaction."),
        dict(key="offtopic", name="🎲┃off-topic", topic="Anything else", slowmode=5),
        dict(key="commands", name="🤖┃bot-commands", topic="/license redeem · /license info · /license reset"),
    ]),
    dict(name="🆘 SUPPORT", perms=open_chat(), channels=[
        dict(key="faq", name="📖┃faq", perms=readonly(), topic="Read this first, most questions are answered here"),
        dict(key="tickets", name="🎫┃tickets", perms=readonly(),
             topic="Private ticket with the staff: purchases, payments, keys, private reports"),
        dict(key="help", name="❓┃help", kind=FORUM,
             topic="One post per problem: version of Mythras, server software, /ac license output, logs"),
        dict(key="bugs", name="🐛┃bug-reports", kind=FORUM,
             topic="Steps to reproduce, server version, Mythras version, config if changed"),
        dict(key="falseflags", name="🎯┃false-flags", kind=FORUM,
             topic="A legit player got flagged? Post the check, VL, ping and a replay if you have one"),
    ]),
    dict(name="💎 PRO", perms=pro_only(), channels=[
        dict(key="prochat", name="💎┃pro-chat", topic="Only for Pro customers"),
        dict(key="prosupport", name="⚡┃pro-support", topic="Priority support: staff answers here first"),
        dict(key="beta", name="🧪┃beta-builds", perms={**pro_only(), "@everyone": (0, VIEW),
                                                       "pro": (READ, SEND), "partner": (READ, SEND)},
             topic="Test builds before release, use them on a test server"),
        dict(key="provoice", name="💎 Pro Lounge", kind=VOICE_CH),
    ]),
    dict(name="🔊 VOICE", perms={"@everyone": (VOICE, 0)}, channels=[
        dict(key="voice1", name="🔊 General", kind=VOICE_CH),
        dict(key="voice2", name="🇮🇹 Italiano", kind=VOICE_CH),
        dict(key="voicesupport", name="🆘 Support (wait here)", kind=VOICE_CH,
             perms={"@everyone": (VIEW | CONNECT | USE_VAD, SPEAK | STREAM),
                    "staff": (VOICE | MOVE_MEMBERS | MUTE_MEMBERS, 0), "dev": (VOICE | MOVE_MEMBERS, 0)}),
        dict(key="afk", name="💤 AFK", kind=VOICE_CH, perms={"@everyone": (VIEW | CONNECT, SPEAK)}),
    ]),
    dict(name="💰 BILLING & SALES", perms=ticket_category(), channels=[]),
    dict(name="🛡️ STAFF", perms=staff_only(), channels=[
        dict(key="staffchat", name="🛡️┃staff-chat", topic="Staff only"),
        dict(key="staffcmd", name="⌨️┃staff-commands", topic="/admin create · revoke · info · plan · extend"),
        dict(key="licenselog", name="📋┃license-log", perms=log_channel(), id_for="discord.log-channel-id",
             topic="Key events from the license server (created, revoked, payments, resets)"),
        dict(key="modlog", name="🔨┃mod-log", perms=log_channel(), topic="Warns, timeouts, bans"),
        dict(key="transcripts", name="📑┃transcripts", perms=log_channel(),
             topic="Transcripts of closed tickets, saved by the ticket bot"),
        dict(key="staffvoice", name="🛡️ Staff", kind=VOICE_CH),
    ]),
]

# ---- server settings ----
SETTINGS = dict(
    verification_level=2,                # 0 none, 1 verified email, 2 + account older than 5 min, 3 + member 10 min
    explicit_content_filter=2,           # scan media from everyone
    default_message_notifications=1,     # only @mentions
    system_channel="welcome",            # "X joined" messages
    afk_channel="afk",
    afk_timeout=900,
)

# ---- messages posted once in empty channels. {#key} = channel mention, {@key} = role mention ----
BLUE, GREEN, TEAL, GOLD, GREY = 0x3498DB, 0x3BA55C, 0x1ABC9C, 0xF1C40F, 0x5C5C5C
MESSAGES = {
    "welcome": [dict(color=BLUE, title="👋 Welcome to Mythras AntiCheat",
                     description=(
                         "Packet-based anticheat for **Paper / Spigot / Folia 1.21 → 26.x**, with a self-learning AI, "
                         "combat mitigations and staff tools.\n\n"
                         "📜 Read the {#rules}\n"
                         "📖 Questions? Check the {#faq}, then open a post in {#help}\n"
                         "💳 Plans and prices: {#plans}\n"
                         "🔗 Website, downloads and docs: {#links}\n"
                         "🇮🇹 Parli italiano? Vieni in {#italiano}\n\n"
                         "Bought **Pro**? Log in on " + SITE + "/dashboard with this Discord account, or use "
                         "`/license redeem` in {#commands}: you get the {@pro} role and the Pro channels."))],
    "rules": [
        dict(color=GOLD, title="📜 Rules",
             description=(
                 "**1.** Be respectful. No insults, harassment, racism or discrimination.\n"
                 "**2.** No spam, flood, walls of caps or mass pings. Don't ping staff for support.\n"
                 "**3.** No advertising (servers, Discords, other anticheats) without permission.\n"
                 "**4.** No cheats, cracks, leaked or resold copies of Mythras. Sharing a license key = key revoked.\n"
                 "**5.** No NSFW, gore, or illegal content.\n"
                 "**6.** Support goes in {#help} with all the details, not in DMs to staff.\n"
                 "**7.** English in the main channels, Italian in {#italiano}.\n"
                 "**8.** Follow the Discord Terms of Service and Community Guidelines.\n\n"
                 "Staff may warn, timeout, kick or ban for anything that breaks the spirit of these rules.")),
        dict(color=GOLD, title="🇮🇹 Regolamento",
             description=(
                 "**1.** Rispetto per tutti: niente insulti, molestie, razzismo o discriminazioni.\n"
                 "**2.** Niente spam, flood, maiuscole o tag di massa. Non taggare lo staff per supporto.\n"
                 "**3.** Niente pubblicità (server, Discord, altri anticheat) senza permesso.\n"
                 "**4.** Niente cheat, crack, copie leakate o rivendute di Mythras. Condividere una key = key revocata.\n"
                 "**5.** Niente NSFW, gore o contenuti illegali.\n"
                 "**6.** Il supporto si chiede in {#help} con tutti i dettagli, non in privato allo staff.\n"
                 "**7.** Inglese nei canali principali, italiano in {#italiano}.\n"
                 "**8.** Rispetta i Termini di Servizio e le Linee guida di Discord.")),
    ],
    "links": [dict(color=BLUE, title="🔗 Links",
                   description=(
                       f"🌐 **Website** · {SITE}\n"
                       f"⬇️ **Download** · {SITE}/download\n"
                       f"📖 **Docs** · {SITE}/docs\n"
                       f"📚 **Wiki** · {SITE}/wiki\n"
                       f"📦 **Changelog** · {SITE}/changelog\n"
                       f"🔑 **Your licenses** · {SITE}/dashboard\n"
                       "🧩 **PacketEvents** (required) · https://modrinth.com/plugin/packetevents\n\n"
                       "Get pinged for new releases: ask staff for the {@updates} role."))],
    "plans": [dict(color=TEAL, title="💳 Plans",
                   description=(
                       "**🆓 Free** · no key needed\n"
                       "Basic movement checks (Speed, Flight/NoFall, Jesus), crash and BadPackets protection, Folia.\n\n"
                       "**💎 Pro** · 19,99 €/month (PayPal subscription)\n"
                       "All movement checks with Prediction, all combat checks (Reach, KillAura, AutoClicker, Aim, "
                       "Backtrack, Crystal PvP...), world and protocol checks, combat mitigations, replays, "
                       "advanced AI with Cloud AI, staff GUI, Discord alerts, API and PlaceholderAPI, "
                       "priority support in {#prosupport}.\n\n"
                       f"Buy on {SITE} and log in with Discord: the key and the {{@pro}} role are yours in a minute."))],
    "addons": [dict(color=TEAL, title="🧩 Add-ons · 5,99 € each, one payment, for ever",
                    description=(
                        "**Anti-XRay** · hides ores the player can't see.\n"
                        "**Anti-ESP** (with Anti-StorageESP) · hides players and chests behind walls.\n\n"
                        "They work on a Free or a Pro key and turn on without a restart (within 5 minutes).\n"
                        f"Buy them on {SITE}/dashboard. Check with `/ac license` in game."))],
    "faq": [dict(color=GREEN, title="📖 FAQ",
                 description=(
                     "**The plugin doesn't start**\n"
                     "Put `packetevents-spigot-2.14.0.jar` (or newer) in `plugins/` next to `Mythras-<version>.jar`.\n\n"
                     "**How do I activate my key?**\n"
                     "`/mlicense <key>` in the console, then restart. `/ac license` shows the plan.\n\n"
                     "**`ip_not_allowed`**\n"
                     f"The key is locked to other IP addresses: change them on {SITE}/dashboard.\n\n"
                     "**I moved to a new machine**\n"
                     f"`Reset servers` on {SITE}/dashboard or `/license reset` in {{#commands}}.\n\n"
                     "**The license server can't be reached**\n"
                     "The server keeps running with the saved plan for up to 72 hours. Check that the machine "
                     "reaches `https://license.mythras.eu`.\n\n"
                     "**A legit player got flagged**\n"
                     "Open a post in {#falseflags} with the check name, VL, ping and a replay.\n\n"
                     f"More in the docs: {SITE}/docs"))],
    "staffchat": [dict(color=GREY, title="🛡️ Staff",
                       description=(
                           "• `/admin` commands go in {#staffcmd} (answers are only visible to you)\n"
                           "• Key events from the license server arrive in {#licenselog}\n"
                           "• Pro customers first: {#prosupport}\n"
                           "• Timeouts before bans, write the reason in {#modlog}"))],
}

# ---- guidelines: one pinned message per channel (a pinned, locked post in forums). Posted again only when
# a pinned message / post with the same title is missing, so they also go into channels that already have messages
GUIDELINES = {
    "general": dict(color=BLUE, title="📌 #general guidelines", description=(
        "• **English only** here. Italiano? → {#italiano}\n"
        "• **No support in this channel**: open a post in {#help} so it doesn't get lost\n"
        "• No advertising of servers, Discords or other anticheats\n"
        "• No arguing about bans from other servers, no drama\n"
        "• Memes and media are fine, keep it friendly\n\n"
        "Full rules: {#rules}")),
    "italiano": dict(color=BLUE, title="📌 Linee guida #italiano", description=(
        "• Qui si scrive **in italiano**, per il resto del server si usa l'inglese\n"
        "• **Niente supporto qui**: apri un post in {#help} (puoi scrivere in italiano), così non si perde\n"
        "• Niente pubblicità di server, Discord o altri anticheat\n"
        "• Niente litigi, drama o discussioni su ban di altri server\n\n"
        "Regolamento completo: {#rules}")),
    "showcase": dict(color=TEAL, title="📌 #showcase guidelines", description=(
        "Show Mythras in action: clips, screenshots, stats, your setup.\n\n"
        "• **Hide IP addresses and players' personal data**; names of cheaters are ok, insults are not\n"
        "• Say what was caught (check name, e.g. `Reach`, `KillAura`)\n"
        "• Your server name is ok, invite links and ads are not\n"
        "• Comments go in the thread of the post, not as new messages")),
    "offtopic": dict(color=BLUE, title="📌 #off-topic guidelines", description=(
        "Anything not about Mythras. The {#rules} still apply.\n\n"
        "• No NSFW, gore, politics or religion\n"
        "• No spam, chain messages or begging for Nitro/ranks\n"
        "• No links to cheats, cracks or \"free Pro keys\" (they are scams)")),
    "suggestions": dict(color=GOLD, title="📌 How to suggest a feature", description=(
        "1. **Search first**: if the idea exists, vote it 👍 instead of posting it again\n"
        "2. **One idea per post**, with a clear title (e.g. `Check for elytra fly speed`)\n"
        "3. Explain **the problem**, not only the solution: what happens on your server today?\n"
        "4. Free or Pro, server software and version if it matters\n\n"
        "Vote with 👍 / 👎. Staff sets the tag: 📅 Planned, ✅ Done, ❌ Rejected (with the reason).\n"
        "No bumping, a popular idea is seen anyway.")),
    "commands": dict(color=GREEN, title="📌 Bot commands", description=(
        "`/license redeem <key>` · links a key to your Discord account (and gives {@pro} for a Pro key)\n"
        "`/license info` · your keys: plan, add-ons, expiry, servers\n"
        "`/license reset <key>` · frees the servers of a key after you move to a new machine\n\n"
        "The answers are **only visible to you**, so your key stays private.\n"
        "⚠️ **Never write a key as a normal message**, in any channel. Shared keys are revoked.")),
    "help": dict(color=GREEN, title="📌 Read before posting", description=(
        "**Before**: check the {#faq} and " + SITE + "/docs, most problems are answered there.\n\n"
        "**One post per problem**, short title (e.g. `Plugin doesn't start on Folia 1.21.4`). Copy this:\n"
        "```\n"
        "Mythras version:   (jar name or /ac version)\n"
        "Plan:              Free / Pro (+ add-ons)\n"
        "Server:            Paper / Spigot / Folia + version\n"
        "PacketEvents:      version\n"
        "Java:              version\n"
        "What happens:\n"
        "What you expected:\n"
        "Logs / errors:     link to https://mclo.gs\n"
        "```\n"
        "• Logs as a **mclo.gs link**, not screenshots or phone photos\n"
        "• **Hide your license key** and IP addresses in logs and screenshots\n"
        "• Don't DM or ping staff: we answer posts in order. Pro customers → {#prosupport}\n"
        "• Fixed? Set the ✅ Solved tag, so others find the answer\n\n"
        "🇮🇹 Puoi scrivere anche in italiano.")),
    "bugs": dict(color=0xE5243B, title="📌 How to report a bug", description=(
        "**Is it a bug?** An error in the console, a check that doesn't work, a command that breaks. "
        "A legit player flagged → {#falseflags}. A question → {#help}.\n\n"
        "**One bug per post.** Copy this:\n"
        "```\n"
        "Mythras version:\n"
        "Server:            Paper / Spigot / Folia + version\n"
        "PacketEvents:      version\n"
        "Steps to reproduce:\n"
        "  1.\n"
        "  2.\n"
        "Expected:\n"
        "Actual:\n"
        "How often:         always / sometimes / once\n"
        "Logs:              https://mclo.gs link\n"
        "Config changed?    which options\n"
        "```\n"
        "🔒 **Bypasses, crash exploits and security problems: never post them here.** Open a ticket in "
        "{#tickets}: a public bypass helps cheaters before we fix it.\n\n"
        "🇮🇹 Puoi scrivere anche in italiano.")),
    "falseflags": dict(color=GOLD, title="📌 How to report a false flag", description=(
        "A **legit** player got flagged, set back or punished. Copy this:\n"
        "```\n"
        "Check:             name and type (e.g. Reach A, Speed B)\n"
        "VL / punishment:\n"
        "Player ping:\n"
        "Player client:     version, Lunar/Badlion/Fabric, ViaVersion?\n"
        "What they did:     e.g. riptide in rain, elytra + firework, ice + soul speed\n"
        "Mythras version:\n"
        "Server:            software + version\n"
        "Config changed?    which options\n"
        "```\n"
        "• A **replay** (Pro) or a video from the player's side helps more than anything\n"
        "• One check per post; several players with the same check → same post\n"
        "• Until it's fixed you can raise the threshold of that check or exempt the player\n\n"
        "🇮🇹 Puoi scrivere anche in italiano.")),
    "prochat": dict(color=TEAL, title="📌 Welcome to Pro", description=(
        "Thanks for supporting Mythras! 💎\n\n"
        "• {#prosupport} · priority support, staff answers there first\n"
        "• {#beta} · test builds before the release\n"
        "• Talk about configs, setups and ideas with other Pro server owners\n\n"
        "Your {@pro} role follows your key: it goes away when the subscription ends and comes back with the "
        "next payment. Keys: " + SITE + "/dashboard")),
    "prosupport": dict(color=TEAL, title="📌 Pro support", description=(
        "Same details as in {#help}, so we can answer at the first message:\n"
        "```\n"
        "Mythras version / add-ons:\n"
        "Server:            software + version\n"
        "PacketEvents / Java:\n"
        "Problem:\n"
        "Logs:              https://mclo.gs link\n"
        "```\n"
        "• Open a **thread** on your message for each problem\n"
        "• **Never post your key**: staff finds it from your Discord account\n"
        "• Urgent (all players kicked, server down)? Write **URGENT** at the start\n\n"
        "🇮🇹 Puoi scrivere anche in italiano.")),
    "beta": dict(color=0x9B59B6, title="📌 Beta builds", description=(
        "• Beta builds can have bugs: **use them on a test server**, not in production\n"
        "• Report problems in the **thread under the release message**, with logs\n"
        "• **Don't share the jars** outside this channel\n"
        "• When the build is stable it's published in {#changelog}")),
    "tickets": dict(color=TEAL, title="🎫 Tickets", description=(
        "A ticket is a **private channel** between you and the staff. Open one with the button below.\n\n"
        "**Open a ticket for**\n"
        "💳 Buying **Pro** or an **add-on**, or paying in another way\n"
        "🧾 Payments, invoices, renewals, refunds\n"
        "🔑 Problems with your key that you can't solve with `/license` in {#commands}\n"
        "🔒 Bypasses, crash exploits and security problems\n"
        "🤝 Partnerships\n\n"
        "**Not for** setup questions or false flags: use {#help} and {#falseflags}, the answers help everyone.\n\n"
        "• **One ticket at a time**, and write the problem in the first message\n"
        "• The staff finds your keys from your Discord account: **no need to send the key**\n"
        "• **Staff never asks for your password** or payments outside " + SITE + " and the ticket\n"
        "• A ticket with no answer from you for 48 hours is closed\n\n"
        "🇮🇹 Puoi scrivere anche in italiano.")),
    "transcripts": dict(color=GREY, title="📌 Transcripts", description=(
        "The ticket bot saves here the transcript of every closed ticket, with who opened and closed it.\n\n"
        "• **Don't delete them**: they are the history of payments and refunds\n"
        "• They contain personal data (names, emails, payments): **never share them outside the staff**\n"
        "• Looking for an old ticket? Search with `in:#📑┃transcripts` and the member's name")),
    "staffcmd": dict(color=GREY, title="📌 Admin commands", description=(
        "`/admin info` · find a key or a customer\n"
        "`/admin create` · new key (Free = only to carry add-ons)\n"
        "`/admin plan` · `/admin extend` · change plan / add days\n"
        "`/admin reset` · free the servers of a key\n"
        "`/admin revoke` · `/admin unrevoke`\n\n"
        "Every action is written in {#licenselog} with your name. "
        "**Write the reason** (ticket, payment, refund) in the command or here.")),
    "modlog": dict(color=GREY, title="📌 Mod log format", description=(
        "One message per action, so everyone knows the history of a member:\n"
        "```\n"
        "User:     @name (ID)\n"
        "Action:   warn / timeout 1h / kick / ban\n"
        "Reason:   rule number + what happened\n"
        "Proof:    message link or screenshot\n"
        "```\n"
        "Warn → timeout → kick → ban. Scams, leaks of Mythras and NSFW → ban at once.")),
}

# forum tags: (name, emoji, only staff can set it)
FORUM_TAGS = {
    "help": [("Solved", "✅", False), ("Waiting for info", "⏳", True), ("Free", "🆓", False), ("Pro", "💎", False)],
    "bugs": [("Investigating", "🔍", True), ("Fixed", "✅", True), ("Not a bug", "❌", True),
             ("Duplicate", "🔁", True)],
    "falseflags": [("Combat", "⚔️", False), ("Movement", "🏃", False), ("World", "🌍", False),
                   ("Fixed", "✅", True)],
    "suggestions": [("Planned", "📅", True), ("Done", "✅", True), ("Rejected", "❌", True)],
}


class Discord:
    def __init__(self, token, apply):
        self.token = token
        self.apply = apply
        self.fake = 0

    def call(self, method, path, body=None, write=True):
        if write and not self.apply:
            self.fake += 1
            return {"id": f"(new-{self.fake})", **(body or {})}
        data = json.dumps(body).encode() if body is not None else None
        for _ in range(6):
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://mythras.eu, 1.0) mythras-setup",
                "X-Audit-Log-Reason": "Mythras server setup",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                if e.code == 429:
                    wait = float(json.loads(raw).get("retry_after", 2)) + 0.25
                    print(f"   rate limited, waiting {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise ApiError(e.code, f"{method} {path}: {e.code} {raw}") from None
        raise ApiError(429, f"{method} {path}: still rate limited")


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def overwrites(spec, roles, everyone_id):
    out = []
    for key, (allow, deny) in spec.items():
        rid = everyone_id if key == "@everyone" else roles[key]["id"]
        if not rid:
            continue
        out.append({"id": rid, "type": 0, "allow": str(allow), "deny": str(deny)})
    return out


def fill(text, roles, channels):
    for key, ch in channels.items():
        text = text.replace("{#" + key + "}", f"<#{ch['id']}>")
    for key, r in roles.items():
        text = text.replace("{@" + key + "}", f"<@&{r['id']}>" if r.get("id") else f"@{r['name']}")
    names = {c["key"]: c["name"] for cat in LAYOUT for c in cat["channels"]}
    text = re.sub(r"\{#(\w+)\}", lambda m: "#" + names.get(m.group(1), m.group(1)), text)  # channel not found
    return text


def has_guideline(d, g, ch, title):
    """True when the channel already has a pinned message (or, in a forum, a post) with this title."""
    if ch.get("type") == FORUM:
        name = title.replace("📌 ", "")[:100]
        threads = d.call("GET", f"/guilds/{g}/threads/active", write=False)["threads"]
        threads += d.call("GET", f"/channels/{ch['id']}/threads/archived/public", write=False)["threads"]
        return any(t.get("parent_id") == ch["id"] and t["name"] == name for t in threads)
    pins = d.call("GET", f"/channels/{ch['id']}/pins", write=False)
    return any(em.get("title") == title for m in pins for em in m.get("embeds", []))


def post_guidelines(d, g, roles, channels, created, apply):
    print("\nGuidelines (pinned)")
    for key, e in GUIDELINES.items():
        if key not in channels:
            print(f"   ? {key}: channel not found, skipped")
            continue
        ch = channels[key]
        embed = {**e, "description": fill(e["description"], roles, channels)}
        if apply and key not in created and has_guideline(d, g, ch, e["title"]):
            print(f"   = {key}: already there")
            continue
        if ch.get("type") == FORUM:
            post = d.call("POST", f"/channels/{ch['id']}/threads",
                          {"name": e["title"].replace("📌 ", "")[:100], "message": {"embeds": [embed]}})
            d.call("PATCH", f"/channels/{post['id']}", {"flags": 2, "locked": True})  # pinned, no replies
        else:
            msg = d.call("POST", f"/channels/{ch['id']}/messages", {"embeds": [embed]})
            d.call("PUT", f"/channels/{ch['id']}/pins/{msg['id']}")
        print(f"   + {key}")


def main():
    ap = argparse.ArgumentParser(description="Build the Mythras Discord server")
    ap.add_argument("guild", help="server ID (right click on the server icon -> Copy Server ID)")
    ap.add_argument("--apply", action="store_true", help="really make the changes (without it: dry run)")
    ap.add_argument("--wipe", action="store_true", help="delete every existing channel first (new servers)")
    ap.add_argument("--only-guidelines", action="store_true",
                    help="only post the pinned guidelines in the channels that already exist, change nothing else")
    ap.add_argument("--add-missing", action="store_true",
                    help="only create the channels and categories that are missing; existing ones, roles and "
                         "server settings are left as they are")
    ap.add_argument("--no-messages", action="store_true", help="don't post welcome/rules/FAQ messages")
    ap.add_argument("--out", default="discord-ids.properties", help="file for the license.properties lines")
    args = ap.parse_args()

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN (Bot page of your application on the Discord Developer Portal).")
    d = Discord(token, args.apply)
    g = args.guild

    guild = d.call("GET", f"/guilds/{g}", write=False)
    me = d.call("GET", "/users/@me", write=False)
    member = d.call("GET", f"/guilds/{g}/members/{me['id']}", write=False)
    all_roles = d.call("GET", f"/guilds/{g}/roles", write=False)
    by_id = {r["id"]: r for r in all_roles}
    my_perms = 0
    for rid in member["roles"] + [g]:
        if rid in by_id:
            my_perms |= int(by_id[rid]["permissions"])
    if not my_perms & ADMINISTRATOR:
        sys.exit("The bot needs the Administrator permission for the setup (invite link with permissions=8). "
                 "You can lower it afterwards to Manage Roles.")
    community = "COMMUNITY" in guild.get("features", [])
    print(f"Server: {guild['name']} · bot: {me['username']} · community: {'yes' if community else 'no'}"
          f" · {'APPLY' if args.apply else 'DRY RUN (add --apply to make the changes)'}\n")

    if args.only_guidelines:
        # roles and channels are found by name, nothing is created or changed
        names = {r["name"]: r for r in all_roles}
        roles = {spec["key"]: names[spec["name"]] for spec in ROLES if spec["name"] in names}
        existing = d.call("GET", f"/guilds/{g}/channels", write=False)
        channels = {}
        for cat in LAYOUT:
            for spec in cat["channels"]:
                ch = next((c for c in existing if c["name"] == spec["name"]), None)
                if ch:
                    channels[spec["key"]] = ch
        for spec in ROLES:  # a missing role would stay as {@key} in the text: write its name instead
            roles.setdefault(spec["key"], {"id": None, "name": spec["name"]})
        post_guidelines(d, g, roles, channels, set(), args.apply)
        if not args.apply:
            print("\nDry run: add --apply to post them.")
        return

    # ---- wipe ----
    existing = d.call("GET", f"/guilds/{g}/channels", write=False)
    if args.wipe and existing:
        print(f"--wipe deletes {len(existing)} channels: " + ", ".join(c["name"] for c in existing))
        if args.apply and input(f'Type the server name "{guild["name"]}" to confirm: ').strip() != guild["name"]:
            sys.exit("Not confirmed, nothing changed.")
        for c in existing:
            d.call("DELETE", f"/channels/{c['id']}")
        existing = []

    # ---- roles ----
    print("Roles")
    roles = {}
    names = {r["name"]: r for r in all_roles}
    top = max((by_id[r]["position"] for r in member["roles"] if r in by_id), default=1)
    for spec in ROLES:
        if args.add_missing:
            roles[spec["key"]] = names.get(spec["name"], {"id": None, "name": spec["name"]})
            print(f"   {'=' if spec['name'] in names else '? (not found)'} {spec['name']}")
            continue
        if spec["name"] in names and names[spec["name"]]["position"] >= top:
            roles[spec["key"]] = names[spec["name"]]  # Discord lets a bot change only the roles under its own
            print(f"   ! {spec['name']}: above the bot role, left as it is")
            continue
        body = {"name": spec["name"], "color": spec["color"], "hoist": spec["hoist"],
                "permissions": str(spec["perms"]), "mentionable": spec.get("mentionable", False)}
        if spec["name"] in names:
            r = names[spec["name"]]
            d.call("PATCH", f"/guilds/{g}/roles/{r['id']}", body)
            roles[spec["key"]] = r
            print(f"   = {spec['name']}")
        else:
            roles[spec["key"]] = d.call("POST", f"/guilds/{g}/roles", body)
            print(f"   + {spec['name']}")

    # order: our roles right under the bot's highest role, in the ROLES order (new roles start at the bottom,
    # so positions are read again after creating them)
    if not args.add_missing:
        if args.apply:
            all_roles = d.call("GET", f"/guilds/{g}/roles", write=False)
            by_id = {r["id"]: r for r in all_roles}
        top = max((by_id[r]["position"] for r in member["roles"] if r in by_id), default=1)
        ours = {x["id"] for x in roles.values()}
        others = sorted((r for r in all_roles if r["id"] != g and r["id"] not in ours and r["position"] < top),
                        key=lambda r: -r["position"])
        order = [roles[s["key"]] for s in ROLES if by_id.get(roles[s["key"]]["id"], {}).get("position", 0) < top]
        order += others  # top to bottom, all below the bot
        positions = [{"id": r["id"], "position": top - 1 - i} for i, r in enumerate(order) if top - 1 - i >= 1]
        if args.apply:
            d.call("PATCH", f"/guilds/{g}/roles", positions)
        print("   ordered under the bot role")
        d.call("PATCH", f"/guilds/{g}/roles/{g}", {"permissions": str(EVERYONE)})
        print("   @everyone: chat, voice, threads; no @everyone pings, no channel/role management")
    print()

    # ---- channels ----
    print("Channels")
    channels = {}
    created = set()

    def ensure(name, kind, parent, perms, topic=None, slowmode=0, tags=()):
        found = next((c for c in existing if c["name"] == name
                      and (c.get("parent_id") == parent or (args.add_missing and kind != CATEGORY))
                      and (c["type"] == kind or {c["type"], kind} <= {TEXT, NEWS, FORUM})), None)
        body = {"name": name, "permission_overwrites": overwrites(perms, roles, g)}
        if kind != CATEGORY:
            body["parent_id"] = parent
        if kind in (TEXT, NEWS, FORUM):
            body["topic"] = topic or ""
            body["rate_limit_per_user"] = slowmode
        def with_tags(have):  # keep the tags already there (posts use them by ID), add the missing ones
            names = {t["name"] for t in have}
            return have + [{"name": n, "emoji_name": e, "moderated": m} for n, e, m in tags if n not in names]
        if found and args.add_missing:
            return found, False
        if found:
            if found["type"] == FORUM and tags:
                body["available_tags"] = with_tags(found.get("available_tags", []))
            if kind == NEWS and community and found["type"] == TEXT:
                body["type"] = NEWS  # made before Community was on
            d.call("PATCH", f"/channels/{found['id']}", body)
            return found, False
        want = kind
        if kind in (NEWS, FORUM) and not community:
            want = TEXT if kind == NEWS else kind
        try:
            extra = {"available_tags": with_tags([])} if want == FORUM and tags else {}
            return d.call("POST", f"/guilds/{g}/channels", {**body, **extra, "type": want}), True
        except ApiError as e:
            if want == TEXT or e.code != 400:
                raise
            print(f"   ! {name}: type {want} refused, creating a text channel")
            return d.call("POST", f"/guilds/{g}/channels", {**body, "type": TEXT}), True

    for cat in LAYOUT:
        c, new = ensure(cat["name"], CATEGORY, None, cat["perms"])
        print(f"   {'+' if new else '='} {cat['name']}")
        for spec in cat["channels"]:
            kind = spec.get("kind", TEXT)
            perms = {**cat["perms"], **spec["perms"]} if "perms" in spec else cat["perms"]
            ch, new = ensure(spec["name"], kind, c["id"], perms, spec.get("topic"), spec.get("slowmode", 0),
                             FORUM_TAGS.get(spec["key"], ()))
            channels[spec["key"]] = ch
            if new:
                created.add(spec["key"])
            print(f"      {'+' if new else '='} {spec['name']}")

    # ---- messages ----
    if not args.no_messages:
        print("\nMessages")
        for key, embeds in MESSAGES.items():
            if not embeds or key not in channels or channels[key].get("type") == FORUM:
                continue
            if args.add_missing and key not in created:
                continue
            if key not in created and args.apply:
                if d.call("GET", f"/channels/{channels[key]['id']}/messages?limit=1", write=False):
                    print(f"   = {key}: not empty, skipped")
                    continue
            out = [{**e, "description": fill(e["description"], roles, channels)} for e in embeds]
            d.call("POST", f"/channels/{channels[key]['id']}/messages", {"embeds": out})
            print(f"   + {key}")

        post_guidelines(d, g, roles, channels, created, args.apply)

    # ---- server settings ----
    s = {k: v for k, v in SETTINGS.items() if k not in ("system_channel", "afk_channel")}
    s["system_channel_id"] = channels[SETTINGS["system_channel"]]["id"]
    s["afk_channel_id"] = channels[SETTINGS["afk_channel"]]["id"]
    if community:
        s["rules_channel_id"] = channels["rules"]["id"]
        s["public_updates_channel_id"] = channels["modlog"]["id"]
    if args.add_missing:
        print("\nSettings: left as they are")
    else:
        if args.apply:
            d.call("PATCH", f"/guilds/{g}", s)
        print("\nSettings: verification medium, media filter for everyone, notifications only @mentions, AFK 15 min")

    # ---- IDs for the license server ----
    lines = [
        f"discord.guild-id={g}",
        f"discord.staff-role-id={roles['staff']['id']}",
        f"discord.role.pro={roles['pro']['id']}",
        f"discord.log-channel-id={channels['licenselog']['id']}",
    ]
    print("\nFor license.properties:\n   " + "\n   ".join(lines))
    if args.apply:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"   (saved in {args.out})")
        print(f"\nDone. Give yourself {ROLES[0]['name']} and other bots {ROLES[4]['name']}.")
        if not community:
            print("Tip: Server Settings -> Enable Community, then run again: announcements become real news "
                  "channels and #rules becomes the rules channel.")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(f"Discord refused: {e}")
    except KeyboardInterrupt:
        sys.exit(1)
