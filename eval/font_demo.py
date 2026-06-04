"""Render a before/after grid proving sub-project B's non-Latin font fix:
left column = old DejaVu (tofu), right column = the new script-aware Noto + RAQM.
Run in the rebuilt image with the repo mounted; writes a PNG to out_core/."""
from PIL import Image, ImageDraw, ImageFont
import backend.api.translate_image as ti

SAMPLES = [
    ("你好世界", "Chinese"), ("こんにちは世界", "Japanese"), ("안녕하세요 세계", "Korean"),
    ("مرحبا بالعالم", "Arabic"), ("שלום עולם", "Hebrew"),
    ("नमस्ते दुनिया", "Hindi"), ("สวัสดีชาวโลก", "Thai"),
]
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

W, H = 760, 58 * len(SAMPLES) + 60
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
hdr = ImageFont.truetype(DEJAVU, 20)
d.text((12, 12), "OLD: DejaVu (tofu)", font=hdr, fill=(180, 0, 0))
d.text((330, 12), "NEW: script-aware Noto + RAQM", font=hdr, fill=(0, 130, 0))

y = 48
for txt, name in SAMPLES:
    old = ImageFont.truetype(DEJAVU, 30)
    fp = ti._script_font(txt, False) or DEJAVU
    try:
        new = ImageFont.truetype(fp, 30, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        new = ImageFont.truetype(fp, 30)
    d.text((12, y), txt, font=old, fill=(180, 0, 0))
    d.text((330, y), txt, font=new, fill=(0, 130, 0))
    d.text((670, y + 6), name, font=ImageFont.truetype(DEJAVU, 14), fill=(120, 120, 120))
    y += 58

out = "/app/eval/locate_anything/out_core/font_demo.png"
img.save(out)
print("saved", out)
