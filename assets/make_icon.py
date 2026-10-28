"""Rebuild assets/icon.png + assets/icon.ico from a square source image (rounded corners made transparent).

Usage: python assets/make_icon.py path/to/source.jpg
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

src = Image.open(sys.argv[1]).convert("RGBA")
side = min(src.size)
src = src.crop(((src.width - side) // 2, (src.height - side) // 2,
                (src.width + side) // 2, (src.height + side) // 2)).resize((1024, 1024), Image.LANCZOS)

ss = 4  # supersampled mask for smooth corners
inset, radius = int(1024 * 0.004), int(1024 * 0.185)
mask = Image.new("L", (1024 * ss, 1024 * ss), 0)
ImageDraw.Draw(mask).rounded_rectangle((inset * ss, inset * ss, (1024 - inset) * ss - 1, (1024 - inset) * ss - 1),
                                       radius=radius * ss, fill=255)
src.putalpha(mask.resize((1024, 1024), Image.LANCZOS))

out = Path(__file__).resolve().parent
src.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
src.save(out / "icon.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64),
                                   (128, 128), (256, 256)])
print("wrote", out / "icon.png", "and", out / "icon.ico")
