"""Data of the public /docs page (customer guide) and of the plans table on the pricing page.

Source: CUSTOMER-GUIDE.md of the plugin (Mythras 1.9). PLAN_MATRIX is shared by the pricing page and the docs,
so the two tables always match. When the plugin changes, update this file.
"""

YES, NO = True, None

# (feature, free, base, pro, enterprise): True = included, None = not included, str = text
PLAN_MATRIX = [
    ("License key", "not needed", "required", "required", "required"),
    ("Servers per key", "-", "1", "1", "unlimited (multi-server)"),
    ("Movement checks", "basic: Speed, Flight/NoFall, Jesus", "complete", "complete", "complete"),
    ("Combat checks", NO, "Reach, hitbox, X-Ray", "+ KillAura, AutoClicker, Timer",
     "all: + Aim, NoSwing, Mace, WindCharge, Backtrack, AimBow, AutoPot, Crystal PvP (CrystalAura, AnchorAura, AutoTotem, AutoSwitch, Surround)"),
    ("World and protocol checks (FreeCam, Scaffold, FastPlace, FastBreak, BadPlace, BadBreak, Inventory, Protocol, Blink...)", NO, YES, YES, YES),
    ("Crash and BadPackets protection", YES, YES, YES, YES),
    ("Setbacks and automatic punishments, staff GUI, Discord webhook", NO, YES, YES, YES),
    ("Replays (kept 7 days)", NO, NO, YES, YES),
    ("AI learning", NO, NO, "standard", "advanced + Mythras Cloud AI (global model)"),
    ("Whitelist and exemptions", NO, NO, YES, YES),
    ("Combat mitigations, Anti-ESP (players), Anti-StorageESP (chests and containers), Anti-XRay (ores, also in caves), health filter", NO, NO, YES, YES),
    ("Cross-server alerts (BungeeCord/Velocity), ban wave, developer API, PlaceholderAPI, Folia", NO, NO, NO, YES),
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
    ("NoSlow", "movement", "base", "Not slowing down while eating, blocking or using items."),
    ("Velocity", "movement", "base", "Taking less knockback than vanilla (AntiKB)."),
    ("Sprint", "movement", "base", "Sprinting while hungry, blind, using items, gliding or sneaking."),
    ("Elytra", "movement", "base", "Starting to glide in impossible states."),
    ("ElytraFly", "movement", "base", "ElytraFly, ElytraSpeed and boosts, recalculated with vanilla glide physics."),
    ("Riptide", "movement", "base", "Riptide launches without a throw or stronger than Riptide III."),
    ("Phase", "movement", "base", "Phase and NoClip: entering or crossing blocks, burrowing into holes."),
    ("PacketFly", "movement", "base", "Huge position offsets and teleports confirmed in advance."),
    ("FastLadder", "movement", "base", "Climbing ladders, vines or scaffolding too fast."),

    ("Reach", "combat", "base", "Hits from too far away or outside the target's hitbox."),
    ("KillAura", "combat", "pro", "Machine-like hit rhythm, hits off the target and perfect accuracy without missed swings."),
    ("AutoClicker", "combat", "pro", "Too many clicks per second and click patterns that are too regular, including randomized clickers."),
    ("Timer", "combat", "pro", "A sped-up game clock (Timer)."),
    ("Aim", "combat", "enterprise", "Duplicate rotations and impossible 360-degree snaps."),
    ("NoSwing", "combat", "enterprise", "Attacks sent without the arm swing."),
    ("Mace", "combat", "enterprise", "Impossible mace smash attacks."),
    ("WindCharge", "combat", "enterprise", "Wind charge knockback that does not match vanilla."),
    ("Backtrack", "combat", "enterprise", "Latency that rises only while fighting (delayed packets)."),
    ("AimBow", "combat", "enterprise", "Bow aimbot: long-range arrows always perfect on the same body point."),
    ("AutoPot", "combat", "enterprise", "Potions thrown with an instant look at the feet and back in the next tick."),

    ("CrystalAura", "crystal", "enterprise", "Crystals placed and broken out of reach or instantly as they appear."),
    ("AnchorAura", "crystal", "enterprise", "AnchorAura and BedAura: placed and used, or charged and exploded, in the same tick."),
    ("AutoTotem", "crystal", "enterprise", "AutoTotem and InvMove."),
    ("AutoSwitch", "crystal", "enterprise", "Silent hotbar switches in the same tick as an action."),
    ("Surround", "crystal", "enterprise", "Surround, AutoTrap and HoleFiller: 3 or more blocks around the feet within 3 ticks."),

    ("XRay", "world", "base", "Mining patterns typical of X-Ray: many ore veins with little stone dug in between. Alerts only by default."),
    ("FreeCam", "world", "base", "Clicks on blocks hidden behind walls (FreeCam, GhostHand): refused and flagged."),
    ("Scaffold", "world", "base", "Scaffold and Tower: rising too fast while placing under the feet, snapping rotations."),
    ("FastPlace", "world", "base", "Placing blocks or crystals faster than vanilla."),
    ("FastBreak", "world", "base", "Breaking blocks faster than vanilla (the break can be cancelled)."),
    ("BadPlace", "world", "base", "Placing blocks too far away, on the wrong face, without looking, or in the air (AirPlace)."),
    ("BadBreak", "world", "base", "Breaking blocks too far away, on the wrong face, several at once, without swinging or looking."),
    ("FastUse", "world", "base", "FastEat, FastBow, BowSpam and FastXP (the action is cancelled)."),

    ("Crash", "protocol", "free", "Malformed packets used for crashes and exploits: cancelled, and the player is kicked."),
    ("BadPackets", "protocol", "free", "Packets a vanilla client can never send."),
    ("Protocol", "protocol", "base", "Packet order inside a tick and ping/pong integrity (MultiMove, PingSpoof and more)."),
    ("Blink", "protocol", "base", "Packets held back and released all at once (setback to where the player stopped)."),
    ("PacketOrder", "protocol", "base", "Actions sent after the movement in the same tick (\"post\" modules)."),
    ("MultiActions", "protocol", "base", "An entity and a block in the same tick, or digging while blocking."),
    ("BadInteract", "protocol", "base", "Interacting with yourself or with a point outside the entity's hitbox."),

    ("Inventory", "other", "base", "AutoArmor, ChestStealer and InventoryCleaner."),
    ("Vehicle", "other", "base", "BoatFly, EntitySpeed and EntityControl (the player is dismounted)."),
    ("AI", "other", "pro", "Self-learning AI that scores each player's fights. Standard on Pro, advanced and trainable on Enterprise."),
]

COMMANDS = [
    # (command, what it does, permission, plan)
    ("/ac alerts", "Turn your alerts on/off", "mythras.alerts", "all"),
    ("/ac info <player>", "Client, version, mods, VL per check, AI score", "mythras.admin", "all"),
    ("/ac history <player> [n]", "Last violations", "mythras.admin", "all"),
    ("/ac clear <player>", "Reset a player's violations", "mythras.admin", "all"),
    ("/ac tp <player>", "Teleport to a player (also by clicking an alert)", "mythras.teleport", "all"),
    ("/ac debug <player>", "Detailed console log for one player", "mythras.admin", "all"),
    ("/ac perf [checks|reset]", "Plugin performance", "mythras.admin", "all"),
    ("/ac reload", "Reload config and messages", "mythras.admin", "all"),
    ("/ac lang [code]", "Show or change the language of all messages: en English, it Italiano, es Español, pt Português, de Deutsch, fr Français, pl Polski, zh 中文 (or any messages_<code>.yml you add); saved in config.yml", "mythras.admin", "all"),
    ("/ac gui", "Players menu", "mythras.admin", "Base+"),
    ("/ac settings", "Menu to turn every check on/off, on Bedrock players or not, and with or without punishments (each change asks for confirmation and is saved in config.yml)", "mythras.admin", "Base+"),
    ("/ac replay <player>", "Menu of the player's replays: left click watches, right click deletes (type confirm, or the word of your language, e.g. conferma, in chat), clock records the last 25 seconds now", "mythras.replay", "Pro+"),
    ("/ac replay list|play <id>|stop", "Replays in chat, watch one by id, stop watching", "mythras.replay", "Pro+"),
    ("/ac ai status|reset", "AI status, reset the model", "mythras.admin", "Pro+"),
    ("/ac ai label <player> <cheat|legit>", "Train the AI", "mythras.admin", "Enterprise"),
    ("/ac ai review", "Suspects of the last hour with the reasons: open their replays, mark them cheating/legit", "mythras.admin", "Enterprise"),
    ("/ac banwave [list|run|clear]", "Queued punishments", "mythras.admin", "Enterprise"),
    ("mlicense <key> (console)", "Set the license key", "console only", "all"),
]

PERMISSIONS = [
    ("mythras.admin", "op", "All commands (includes the three below)"),
    ("mythras.alerts", "op", "Receive alerts"),
    ("mythras.teleport", "op", "Teleport from alerts and the GUI"),
    ("mythras.replay", "op", "Watch replays"),
    ("mythras.bypass", "false", "The player is not checked. Do not give it to normal players or with *"),
]

PLAN_ORDER = ("free", "base", "pro", "enterprise")


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
