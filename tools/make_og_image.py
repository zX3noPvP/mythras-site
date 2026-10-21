"""Builds static/og.png (1200x630), the preview image shown when the site link is shared."""
import sys
from PIL import Image, ImageDraw, ImageFont
out = sys.argv[1]
W, H = 1200, 630
img = Image.new("RGB", (W, H), "#000000")
d = ImageDraw.Draw(img)
red = (225, 15, 31)
# subtle grid
for x in range(0, W, 60):
    d.line([(x, 0), (x, H)], fill=(18, 18, 18))
for y in range(0, H, 60):
    d.line([(0, y), (W, y)], fill=(18, 18, 18))
# shield mark
cx, cy = 150, 250
d.polygon([(cx - 60, cy - 70), (cx + 60, cy - 70), (cx + 60, cy + 10), (cx, cy + 75), (cx - 60, cy + 10)], fill=red)
d.polygon([(cx - 32, cy - 40), (cx - 16, cy - 40), (cx, cy - 12), (cx + 16, cy - 40), (cx + 32, cy - 40), (cx + 32, cy + 20), (cx + 16, cy + 20), (cx + 16, cy - 8), (cx, cy + 14), (cx - 16, cy - 8), (cx - 16, cy + 20), (cx - 32, cy + 20)], fill="#ffffff")
big = ImageFont.load_default(size=92)
mid = ImageFont.load_default(size=40)
small = ImageFont.load_default(size=30)
d.text((250, 175), "MYTHRAS", font=big, fill="#ffffff")
d.text((254, 280), "AntiCheat for Minecraft servers", font=mid, fill=(200, 200, 200))
d.text((90, 420), "Stop the cheaters.", font=mid, fill="#ffffff")
d.text((90, 470), "Keep the fair players.", font=mid, fill=red)
d.text((90, 560), "Paper  ·  Spigot  ·  Folia     Minecraft 1.21 - 26.x", font=small, fill=(160, 160, 160))
d.rectangle([0, H - 8, W, H], fill=red)
img.save(out, optimize=True)
print("saved", out)
