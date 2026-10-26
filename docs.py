"""Data of the public /docs page (customer guide) and of the plans table on the pricing page.

Source: CUSTOMER-GUIDE.md of the plugin (Mythras 1.25), plans as of 2026-10: Free, Pro and the add-ons Anti-XRay and Anti-ESP. PLAN_MATRIX is shared by the pricing page and the docs,
so the two tables always match. When the plugin changes, update this file.
"""

YES, NO = True, None

# (feature, free, pro): True = included, None = not included, str = text. Add-ons work with both plans.
PLAN_MATRIX = [
    ("Price", "free", "19,99 €/month (PayPal subscription)"),
    ("License key", "not needed (only to carry add-ons)", "required"),
    ("Servers per key", "1 (key with add-ons)", "1"),
    ("Movement checks", "basic: Speed, Flight/NoFall, Jesus", "complete, with Prediction"),
    ("Combat checks", NO, "all: Reach, hitbox, KillAura, AutoClicker, Timer, Aim, NoSwing, Mace, WindCharge, Backtrack, AimBow, AutoPot, Crystal PvP (CrystalAura, AnchorAura, AutoTotem, AutoSwitch, Surround)"),
    ("World and protocol checks (FreeCam, Scaffold, FastPlace, FastBreak, BadPlace, BadBreak, Inventory, Protocol, Blink, X-Ray...)", NO, YES),
    ("Crash and BadPackets protection", YES, YES),
    ("Setbacks and automatic punishments, staff GUI, Discord webhook, customer panel", NO, YES),
    ("Replays (kept 7 days)", NO, YES),
    ("AI learning + Mythras Cloud AI (global model)", NO, YES),
    ("Whitelist and exemptions", NO, YES),
    ("Combat mitigations, health filter", NO, YES),
    ("Cross-server alerts (BungeeCord/Velocity), developer API, PlaceholderAPI, Folia", NO, YES),
    ("Anti-XRay (ores hidden, also in caves)", "add-on", "add-on"),
    ("Anti-ESP and Anti-StorageESP (players and containers hidden behind walls)", "add-on", "add-on"),
]

CATEGORIES = [
    ("movement", "Movement"),
    ("combat", "Combat"),
    ("crystal", "Crystal PvP"),
    ("world", "World and building"),
    ("protocol", "Packets and protocol"),
    ("other", "Inventory, vehicles and AI"),
]

# (name, category, lowest plan, what it detects). Plans follow PLAN_MATRIX.
CHECKS = [
    ("Speed", "movement", "free", "Moving faster than vanilla physics allow, including air strafe."),
    ("Flight", "movement", "free", "Flying, hovering and NoFall."),
    ("Jesus", "movement", "free", "Walking on water or lava, swimming or rising too fast in water."),
    ("NoSlow", "movement", "pro", "Not slowing down while eating, blocking or using items."),
    ("Velocity", "movement", "pro", "Taking less knockback than vanilla (AntiKB)."),
    ("Sprint", "movement", "pro", "Sprinting while hungry, blind, using items, gliding or sneaking."),
    ("Elytra", "movement", "pro", "Starting to glide in impossible states."),
    ("ElytraFly", "movement", "pro", "ElytraFly, ElytraSpeed and boosts, recalculated with vanilla glide physics."),
    ("Riptide", "movement", "pro", "Riptide launches without a throw or stronger than Riptide III."),
    ("Phase", "movement", "pro", "Phase and NoClip: entering or crossing blocks, burrowing into holes."),
    ("PacketFly", "movement", "pro", "Huge position offsets and teleports confirmed in advance."),
    ("FastLadder", "movement", "pro", "Climbing ladders, vines or scaffolding too fast."),

    ("Reach", "combat", "pro", "Hits from too far away or outside the target's hitbox."),
    ("KillAura", "combat", "pro", "Machine-like hit rhythm, hits off the target and perfect accuracy without missed swings."),
    ("AutoClicker", "combat", "pro", "Too many clicks per second and click patterns that are too regular, including randomized clickers."),
    ("Timer", "combat", "pro", "A sped-up game clock (Timer)."),
    ("Aim", "combat", "pro", "Duplicate rotations and impossible 360-degree snaps."),
    ("NoSwing", "combat", "pro", "Attacks sent without the arm swing."),
    ("Mace", "combat", "pro", "Impossible mace smash attacks."),
    ("WindCharge", "combat", "pro", "Wind charge knockback that does not match vanilla."),
    ("Backtrack", "combat", "pro", "Latency that rises only while fighting (delayed packets)."),
    ("AimBow", "combat", "pro", "Bow aimbot: long-range arrows always perfect on the same body point."),
    ("AutoPot", "combat", "pro", "Potions thrown with an instant look at the feet and back in the next tick."),

    ("CrystalAura", "crystal", "pro", "Crystals placed and broken out of reach or instantly as they appear."),
    ("AnchorAura", "crystal", "pro", "AnchorAura and BedAura: placed and used, or charged and exploded, in the same tick."),
    ("AutoTotem", "crystal", "pro", "AutoTotem and InvMove."),
    ("AutoSwitch", "crystal", "pro", "Silent hotbar switches in the same tick as an action."),
    ("Surround", "crystal", "pro", "Surround, AutoTrap and HoleFiller: 3 or more blocks around the feet within 3 ticks."),

    ("XRay", "world", "pro", "Mining patterns typical of X-Ray: many ore veins with little stone dug in between. Alerts only by default."),
    ("FreeCam", "world", "pro", "Clicks on blocks hidden behind walls (FreeCam, GhostHand): refused and flagged."),
    ("Scaffold", "world", "pro", "Scaffold and Tower: rising too fast while placing under the feet, snapping rotations."),
    ("FastPlace", "world", "pro", "Placing blocks or crystals faster than vanilla."),
    ("FastBreak", "world", "pro", "Breaking blocks faster than vanilla (the break can be cancelled)."),
    ("BadPlace", "world", "pro", "Placing blocks too far away, on the wrong face, without looking, or in the air (AirPlace)."),
    ("BadBreak", "world", "pro", "Breaking blocks too far away, on the wrong face, several at once, without swinging or looking."),
    ("FastUse", "world", "pro", "FastEat, FastBow, BowSpam and FastXP (the action is cancelled)."),

    ("Crash", "protocol", "free", "Malformed packets used for crashes and exploits: cancelled, and the player is kicked."),
    ("BadPackets", "protocol", "free", "Packets a vanilla client can never send."),
    ("Protocol", "protocol", "pro", "Packet order inside a tick and ping/pong integrity (MultiMove, PingSpoof and more)."),
    ("Blink", "protocol", "pro", "Packets held back and released all at once (setback to where the player stopped)."),
    ("PacketOrder", "protocol", "pro", "Actions sent after the movement in the same tick (\"post\" modules)."),
    ("MultiActions", "protocol", "pro", "An entity and a block in the same tick, or digging while blocking."),
    ("BadInteract", "protocol", "pro", "Interacting with yourself or with a point outside the entity's hitbox."),

    ("Inventory", "other", "pro", "AutoArmor, ChestStealer and InventoryCleaner."),
    ("Vehicle", "other", "pro", "BoatFly, EntitySpeed and EntityControl (the player is dismounted)."),
    ("AI", "other", "pro", "Self-learning AI that scores each player's fights; tunable and trainable by your staff."),
]

COMMANDS = [
    # (command, what it does, permission, plan)
    ("/ac alerts", "Turn your alerts on/off", "mythras.alerts", "all"),
    ("/ac info <player>", "Client, version, mods, VL per check, AI score", "mythras.admin", "all"),
    ("/ac history <player> [n] [check]", "Last violations (of one check only, if given)", "mythras.admin", "all"),
    ("/ac clear <player>", "Reset a player's violations", "mythras.admin", "all"),
    ("/ac tp <player>", "Teleport to a player (also by clicking an alert)", "mythras.teleport", "all"),
    ("/ac debug <player>", "Detailed console log for one player", "mythras.admin", "all"),
    ("/ac perf [checks|reset]", "Plugin performance", "mythras.admin", "all"),
    ("/ac reload", "Reload config and messages", "mythras.admin", "all"),
    ("/ac lang [code]", "Show or change the language of all messages: en English, it Italiano, es Español, pt Português, de Deutsch, fr Français, pl Polski, zh 中文 (or any messages_<code>.yml you add); saved in config.yml", "mythras.admin", "all"),
    ("/ac gui", "Players menu", "mythras.admin", "Pro"),
    ("/ac settings", "Menu to turn every check on/off, on Bedrock players or not, and with or without punishments (each change asks for confirmation and is saved in config.yml)", "mythras.admin", "Pro"),
    ("/ac replay <player>", "Menu of the player's replays: left click watches, right click deletes (type confirm, or the word of your language, e.g. conferma, in chat), clock records the last 25 seconds now", "mythras.replay", "Pro"),
    ("/ac replay list|play <id>|stop", "Replays in chat, watch one by id, stop watching", "mythras.replay", "Pro"),
    ("/ac ai status|reset", "AI status, reset the model", "mythras.admin", "Pro"),
    ("/ac ai label <player> <cheat|legit>", "Train the AI", "mythras.admin", "Pro"),
    ("/ac ai review", "Suspects of the last hour with the reasons: open their replays, mark them cheating/legit", "mythras.admin", "Pro"),
    ("mlicense <key> (console)", "Set the license key", "console only", "all"),
]

PERMISSIONS = [
    ("mythras.admin", "op", "All commands (includes the three below)"),
    ("mythras.alerts", "op", "Receive alerts"),
    ("mythras.teleport", "op", "Teleport from alerts and the GUI"),
    ("mythras.replay", "op", "Watch replays"),
    ("mythras.bypass", "false", "The player is not checked. Do not give it to normal players or with *"),
]

PLAN_ORDER = ("free", "pro")


def counts():
    """Number of checks included in each plan."""
    rank = {p: i for i, p in enumerate(PLAN_ORDER)}
    return {p: sum(1 for c in CHECKS if rank[c[2]] <= rank[p]) for p in PLAN_ORDER}


def texts():
    """English strings of this module shown (translated) on the docs page."""
    out = {label for _, label in CATEGORIES} | {c[3] for c in CHECKS}
    out |= {c[1] for c in COMMANDS} | {p[2] for p in PERMISSIONS}
    return out | matrix_texts()


def matrix_texts():
    """English strings of PLAN_MATRIX shown (translated) on the pricing page."""
    out = set()
    for row in PLAN_MATRIX:
        out.update(v for v in row if isinstance(v, str) and any(ch.isalpha() for ch in v))
    return out
