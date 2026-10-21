"""Render the demo replay video shown on the site.

This is a simulated, stylised replay (not real gameplay). It follows the same
timeline as the event list on the home page, so the site can sync to it.

Usage:  .venv\\Scripts\\python.exe tools\\render_replay.py
Needs:  pip install -r tools/requirements.txt
Output: static/video/replay-demo.mp4 and static/video/replay-poster.jpg
"""

import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "static" / "video"

W, H, SS, FPS = 1280, 720, 2, 30          # SS = supersampling factor for smooth edges
SW, SH = W * SS, H * SS
DURATION = 12.0

# timeline (seconds) — keep in sync with the event list in templates/index.html
SPRINT_T = 2.6
JUMP_START, JUMP_END = 5.4, 6.0
ANOMALY_START, ANOMALY_END = 7.0, 8.6
FLAG_T, AI_T, SAVED_T = 7.4, 8.1, 9.6

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (225, 15, 31)
RED_TEXT = (255, 51, 68)
MUTED = (163, 163, 163)
LINE = (58, 58, 58)

FONT_DIR = Path("C:/Windows/Fonts")


def font(name, size):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size * SS)
    except OSError:
        return ImageFont.load_default(size * SS)


MONO = font("consola.ttf", 18)
MONO_B = font("consolab.ttf", 18)
MONO_B_BIG = font("consolab.ttf", 24)
UI_B = font("segoeuib.ttf", 17)
UI_SMALL = font("segoeuib.ttf", 12)


# ---------------------------------------------------------------- motion

# (start, end, speed weight): walk, sprint, speed hack, sprint
SEGMENTS = [(0.0, SPRINT_T, 0.6), (SPRINT_T, ANOMALY_START, 1.0),
            (ANOMALY_START, ANOMALY_END, 2.0), (ANOMALY_END, DURATION, 1.0)]


def distance(t):
    return sum(w * max(0.0, min(t, b) - a) for a, b, w in SEGMENTS)


TOTAL = distance(DURATION)


def player_pos(t):
    p = distance(t) / TOTAL
    x = 2 + p * 46
    z = 9 + 3.2 * math.sin(p * math.tau * 1.4)
    y = 0.0
    if JUMP_START <= t <= JUMP_END:
        y = 1.25 * math.sin(math.pi * (t - JUMP_START) / (JUMP_END - JUMP_START))
    return x, y, z


def in_anomaly(t):
    return ANOMALY_START <= t <= ANOMALY_END


def speed_text(t):
    if t < SPRINT_T:
        return "4.3 b/s"
    if in_anomaly(t):
        return "14.2 b/s"
    return "7.1 b/s"


# ---------------------------------------------------------------- camera

PITCH = math.radians(33)
SIN_P, COS_P = math.sin(PITCH), math.cos(PITCH)
FOCAL = 1.25 * SH
HORIZON = SH * 0.52
HEADER_H = 118  # px of the top HUD bar (before supersampling)


def camera(t):
    px, _, _ = player_pos(t)
    return px - 1.5, 9.0, -4.0


def project(pt, cam):
    dx, dy, dz = pt[0] - cam[0], pt[1] - cam[1], pt[2] - cam[2]
    zc = max(0.1, -dy * SIN_P + dz * COS_P)
    yc = dy * COS_P + dz * SIN_P
    return SW / 2 + FOCAL * dx / zc, HORIZON - FOCAL * yc / zc, zc


# ---------------------------------------------------------------- world

OBSTACLES = [(6, 14, 2), (11, 3, 1), (15, 16, 3), (20, 5, 1), (24, 13, 1), (27, 17, 2),
             (32, 3, 2), (36, 15, 1), (41, 5, 3), (45, 14, 2), (50, 4, 1), (53, 16, 2)]
WALL_Z = 21
WALL = [(x, WALL_Z, 1) for x in range(-14, 66) if x % 7 != 3]

CUBE = {"top": (24, 24, 24), "front": (12, 12, 12), "side": (17, 17, 17)}
CUBE_EDGE = (55, 55, 55)
PLAYER = {"top": (255, 255, 255), "front": (226, 226, 226), "side": (186, 186, 186)}
PLAYER_HOT = {"top": (255, 51, 68), "front": (225, 15, 31), "side": (160, 8, 20)}


def box_faces(x0, y0, z0, x1, y1, z1, cam):
    faces = []
    if cam[0] < x0:
        faces.append(([(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)], "side"))
    elif cam[0] > x1:
        faces.append(([(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)], "side"))
    if cam[2] < z0:
        faces.append(([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)], "front"))
    if cam[1] > y1:
        faces.append(([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], "top"))
    return faces


def draw_box(d, box, cam, palette, edge):
    for poly, kind in box_faces(*box, cam):
        pts = [project(p, cam)[:2] for p in poly]
        d.polygon(pts, fill=palette[kind], outline=edge)


def draw_ground(d, cam):
    cx = cam[0]
    for z in range(0, WALL_Z + 1):
        a = project((cx - 30, 0, z), cam)
        b = project((cx + 30, 0, z), cam)
        shade = int(44 - 26 * (z / WALL_Z))
        d.line([a[:2], b[:2]], fill=(shade, shade, shade), width=SS)
    for x in range(int(cx) - 30, int(cx) + 31):
        a = project((x, 0, 0), cam)
        b = project((x, 0, WALL_Z), cam)
        d.line([a[:2], b[:2]], fill=(30, 30, 30), width=SS)


def draw_trail(d, t, cam):
    start = max(0.0, t - 4.0)
    steps = int((t - start) * FPS)
    prev = None
    for i in range(steps + 1):
        ts = start + (t - start) * i / max(1, steps)
        x, _, z = player_pos(ts)
        pt = project((x, 0.02, z), cam)[:2]
        if prev:
            age = (t - ts) / 4.0
            if in_anomaly(ts):
                color = RED_TEXT
            else:
                g = int(235 - 170 * age)
                color = (g, g, g)
            d.line([prev, pt], fill=color, width=3 * SS)
        prev = pt


def draw_speed_lines(d, t, cam, frame):
    px, py, pz = player_pos(t)
    for k in range(5):
        y = py + 0.35 + 0.3 * k
        z = pz + (k - 2) * 0.12
        length = 1.4 + 0.5 * ((k * 37 + frame) % 4)
        a = project((px - 0.6, y, z), cam)[:2]
        b = project((px - 0.6 - length, y, z), cam)[:2]
        d.line([a, b], fill=RED_TEXT if k % 2 else (150, 150, 150), width=2 * SS)


def ring_points(cx, cz, r, cam, n=48):
    return [project((cx + r * math.cos(i * math.tau / n), 0.03, cz + r * math.sin(i * math.tau / n)), cam)[:2]
            for i in range(n)]


# ---------------------------------------------------------------- HUD helpers

def s(v):
    return v * SS


def text_w(d, txt, fnt):
    l, _, r, _ = d.textbbox((0, 0), txt, font=fnt)
    return r - l


def ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def draw_hud(d, t):
    # solid header bar so the HUD never sits on top of the scene
    d.rectangle([0, 0, SW, s(HEADER_H)], fill=BLACK)
    d.line([(0, s(HEADER_H)), (SW, s(HEADER_H))], fill=(34, 34, 34), width=SS)

    # top-left: recording + clip id
    d.ellipse([s(32), s(30), s(44), s(42)], fill=RED)
    d.text((s(52), s(26)), "REC", font=MONO_B, fill=RED_TEXT)
    d.text((s(100), s(26)), "FLAG #4821 · PLAYER_01", font=MONO, fill=WHITE)

    # top-right: timecode, live speed, demo label
    tc = f"00:{t:04.1f}"
    d.text((SW - s(32) - text_w(d, tc, MONO_B_BIG), s(22)), tc, font=MONO_B_BIG, fill=WHITE)
    sp = speed_text(t)
    sw = text_w(d, sp, MONO_B) + s(20)
    x0, y0 = SW - s(32) - sw, s(58)
    if in_anomaly(t):
        d.rectangle([x0, y0, x0 + sw, y0 + s(28)], fill=RED)
        d.text((x0 + s(10), y0 + s(4)), sp, font=MONO_B, fill=WHITE)
    else:
        d.rectangle([x0, y0, x0 + sw, y0 + s(28)], outline=LINE, width=SS)
        d.text((x0 + s(10), y0 + s(4)), sp, font=MONO_B, fill=WHITE)
    demo = "SIMULATED DEMO"
    d.text((SW - s(32) - text_w(d, demo, UI_SMALL), s(96)), demo, font=UI_SMALL, fill=MUTED)

    # bottom-left: status chip, then the AI verdict card
    if t < AI_T:
        flagged = t >= FLAG_T
        label = "FLAGGED · SPEED" if flagged else "MONITORING · MOVEMENT CHECKS"
        w = text_w(d, label, UI_SMALL) + s(36)
        x0, y0 = s(32), SH - s(64)
        d.rectangle([x0, y0, x0 + w, y0 + s(32)], fill=RED if flagged else BLACK,
                    outline=RED if flagged else LINE, width=SS)
        d.ellipse([x0 + s(12), y0 + s(12), x0 + s(20), y0 + s(20)], fill=WHITE if flagged else MUTED)
        d.text((x0 + s(28), y0 + s(8)), label, font=UI_SMALL, fill=WHITE)
    else:
        k = ease_out((t - AI_T) / 0.35)
        x0 = s(32) - int(s(40) * (1 - k))
        y0 = SH - s(124)
        d.rectangle([x0, y0, x0 + s(430), y0 + s(92)], fill=BLACK, outline=LINE, width=SS)
        d.rectangle([x0, y0, x0 + s(4), y0 + s(92)], fill=RED)
        d.text((x0 + s(22), y0 + s(14)), "AI LEARNING · VERDICT", font=UI_SMALL, fill=MUTED)
        bw = text_w(d, "CONFIRMED", UI_SMALL) + s(20)
        d.rectangle([x0 + s(22), y0 + s(42), x0 + s(22) + bw, y0 + s(70)], fill=RED)
        d.text((x0 + s(32), y0 + s(48)), "CONFIRMED", font=UI_SMALL, fill=WHITE)
        d.text((x0 + s(34) + bw, y0 + s(44)), "lag ruled out · ping 42 ms", font=UI_B, fill=WHITE)

    # bottom-right: clip saved toast
    if t >= SAVED_T:
        k = ease_out((t - SAVED_T) / 0.35)
        msg = "Clip #4821 saved for staff review"
        w = text_w(d, msg, UI_B) + s(64)
        x0 = SW - s(32) - w
        y0 = SH - s(80) + int(s(24) * (1 - k))
        d.rectangle([x0, y0, x0 + w, y0 + s(48)], fill=WHITE)
        cx, cy = x0 + s(22), y0 + s(24)
        d.line([(cx - s(7), cy), (cx - s(2), cy + s(6)), (cx + s(8), cy - s(6))], fill=RED, width=3 * SS)
        d.text((x0 + s(42), y0 + s(12)), msg, font=UI_B, fill=BLACK)


# ---------------------------------------------------------------- frame

FLAG_POS = player_pos(FLAG_T)


def render(t, frame):
    img = Image.new("RGB", (SW, SH), BLACK)
    d = ImageDraw.Draw(img)
    cam = camera(t)

    draw_ground(d, cam)
    draw_trail(d, t, cam)

    # flag ring on the ground where the anomaly was confirmed
    if t >= FLAG_T:
        grow = ease_out((t - FLAG_T) / 0.5)
        r = 1.6 - 0.7 * grow
        pts = ring_points(FLAG_POS[0], FLAG_POS[2], r, cam)
        d.polygon(pts, outline=RED_TEXT, width=3 * SS)

    # depth-sorted solids: obstacles, wall, player
    px, py, pz = player_pos(t)
    objects = []
    for (x, z, h) in OBSTACLES + WALL:
        if abs(x - cam[0]) > 22:
            continue
        for level in range(h):
            box = (x, level, z, x + 1, level + 1, z + 1)
            depth = project((x + .5, level + .5, z + .5), cam)[2]
            objects.append((depth, "cube", box))
    body = (px - 0.34, py, pz - 0.22, px + 0.34, py + 1.35, pz + 0.22)
    head = (px - 0.28, py + 1.35, pz - 0.28, px + 0.28, py + 1.9, pz + 0.28)
    objects.append((project((px, py + 0.9, pz), cam)[2], "player", (body, head)))
    objects.sort(key=lambda o: -o[0])

    hot = in_anomaly(t)
    for _, kind, data in objects:
        if kind == "cube":
            draw_box(d, data, cam, CUBE, CUBE_EDGE)
        else:
            if hot:
                draw_speed_lines(d, t, cam, frame)
            for part in data:
                draw_box(d, part, cam, PLAYER_HOT if hot else PLAYER, BLACK)

    # label pinned above the flag point
    if t >= FLAG_T:
        base = project((FLAG_POS[0], 0.03, FLAG_POS[2]), cam)[:2]
        anchor = project((FLAG_POS[0], 3.0, FLAG_POS[2]), cam)[:2]
        d.line([base, anchor], fill=RED_TEXT, width=2 * SS)
        label = "SPEED 14.2 b/s · max 7.1"
        lw = text_w(d, label, MONO_B) + s(24)
        lx, ly = anchor[0] + s(6), anchor[1] - s(34)
        d.rectangle([lx, ly, lx + lw, ly + s(34)], fill=RED)
        d.text((lx + s(12), ly + s(7)), label, font=MONO_B, fill=WHITE)

    draw_hud(d, t)
    return img.resize((W, H), Image.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "replay-demo.mp4"
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames = int(DURATION * FPS)
    for i in range(frames):
        t = i / FPS
        proc.stdin.write(render(t, i).tobytes())
        if i % FPS == 0:
            print(f"rendered {i}/{frames}")
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")

    render(7.9, int(7.9 * FPS)).save(OUT_DIR / "replay-poster.jpg", quality=88)
    print(f"done: {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
