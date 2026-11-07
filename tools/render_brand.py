#!/usr/bin/env python3
"""Reproducibly render approved PowerLink vector masters into Home Assistant brand PNGs.
Run: python tools/render_brand.py (requires cairosvg + Pillow).
"""
from pathlib import Path
from io import BytesIO
from PIL import Image
import cairosvg
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"branding"
BRAND=ROOT/"custom_components/powerlink/brand"
BRAND.mkdir(parents=True,exist_ok=True)
def convert(source,output,w,h):
    data=cairosvg.svg2png(url=str(SOURCE/source),output_width=w,output_height=h)
    img=Image.open(BytesIO(data)).convert("RGBA")
    assert img.size==(w,h)
    img.save(BRAND/output,format="PNG",optimize=True)
for prefix,source in [("","powerlink-glyph.svg"),("dark_","powerlink-glyph.svg")]:
    # The dark_ naming matches Home Assistant's dark-theme backgrounds.
    pass
for name,source in {
    "icon.png":"powerlink-glyph-light.svg",
    "icon@2x.png":"powerlink-glyph-light.svg",
    "dark_icon.png":"powerlink-glyph.svg",
    "dark_icon@2x.png":"powerlink-glyph.svg",
    "logo.png":"powerlink-app-icon.svg",
    "logo@2x.png":"powerlink-app-icon.svg",
    "dark_logo.png":"powerlink-app-icon.svg",
    "dark_logo@2x.png":"powerlink-app-icon.svg",
}.items():
    size=512 if "@2x" in name else 256
    convert(source,name,size,size)
print("Rendered eight PowerLink Home Assistant brand PNGs")
