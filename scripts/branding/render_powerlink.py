#!/usr/bin/env python3
"""Render only Home Assistant's mandatory raster art, all from the *approved* SVG.

No alternative mark, no simplified glyph, no resized raster inputs. SVG stays the
source for repository/README/website artwork. PNG exports are lossless, full RGBA.

Run from any directory: python scripts/branding/render_powerlink.py
Verify committed art: python scripts/branding/render_powerlink.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "assets" / "brand"
INTEGRATION_BRAND = ROOT / "custom_components" / "powerlink" / "brand"
APPROVED_SHA256 = "d7184cc8df4d35311fb68c6eaff89632d30b570594b39e20f73754f4ab60e200"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def approved_svg() -> bytes:
    content = (BRAND / "powerlink-mark.svg").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != APPROVED_SHA256:
        raise RuntimeError("PowerLink master SVG differs from the approved v6 asset; refusing to substitute a variant")
    root = ET.fromstring(content)
    if root.tag != f"{{{NS}}}svg" or root.attrib.get("viewBox") != "0 0 1000 1000":
        raise ValueError("The approved master SVG has an unexpected document geometry")
    if any(element.tag == f"{{{NS}}}image" for element in root.iter()):
        raise ValueError("The master must remain vector, not embed a raster image")
    return content


def inner_content(master: bytes) -> str:
    """Copy the ORIGINAL vector elements unchanged into a larger SVG composition."""
    node = ET.fromstring(master)
    return "".join(ET.tostring(item, encoding="unicode") for item in node)


def app_icon(master: bytes) -> str:
    """The approved app-icon treatment, wrapping the same mark, with no small-icon edit."""
    content = master.decode("utf-8")
    head, tail = content.split("</defs>", 1)
    body = tail.rsplit("</svg>", 1)[0]
    return head + '''</defs>
<rect width="1000" height="1000" rx="168" fill="url(#tileBackground)"/>
<rect x="14" y="14" width="972" height="972" rx="155" fill="none" stroke="url(#tileRim)" stroke-width="5" opacity=".79"/>
<g transform="translate(500 500) scale(.87) translate(-500 -500)">
''' + body + '''</g>
</svg>
'''


def lockup(master: bytes, *, banner: bool) -> str:
    """Wordmark + identical master glyph; not an alternate glyph treatment."""
    width, height = (1920, 560) if banner else (1440, 400)
    frame = f'<rect width="{width}" height="{height}" rx="30" fill="#050B24"/>' if banner else ""
    # One nested native SVG: a direct copy of the approved shapes and gradients.
    emblem_size = 500 if banner else 370
    x = 24 if banner else 0
    y = 24 if banner else 14
    title_x = 584 if banner else 433
    title_y = 316 if banner else 223
    font_size = 153 if banner else 115
    link_x = 1177 if banner else 880
    caption = (f'<text x="{title_x+6}" y="405" font-family="DejaVu Sans,Arial,sans-serif" '
               'font-size="33" letter-spacing="6" fill="#B8CFF6">MEDIA   •   DEVICES   •   HANDOFF</text>') if banner else ""
    # The public README banner uses the approved uppercase POWERLINK treatment.
    # Keep it generated from the same approved glyph, but do not silently
    # replace the reviewed banner with the older mixed-case lockup.
    if banner:
        return (
            f'<svg xmlns="{NS}" width="1920" height="560" viewBox="0 0 1920 560">'
            f'{frame}<svg x="24" y="24" width="500" height="500" viewBox="0 0 1000 1000">'
            f'{inner_content(master)}</svg>'
            '<path d="M565 115 V442" stroke="#177BFB" stroke-width="3" opacity=".7"/>\n'
            '<text x="611" y="302" font-family="DejaVu Sans,Arial,sans-serif" font-size="157" font-weight="900" font-style="italic" letter-spacing="-6" fill="white">POWER</text>\n'
            '<text x="1220" y="302" font-family="DejaVu Sans,Arial,sans-serif" font-size="157" font-weight="900" font-style="italic" letter-spacing="-6" fill="#09C6FF">LINK</text>\n'
            '<text x="659" y="391" font-family="DejaVu Sans,Arial,sans-serif" font-size="36" letter-spacing="6" fill="#C4D5F4">MEDIA  •  DEVICES  •  HANDOFF</text>'
            '</svg>'
        )
    return (f'<svg xmlns="{NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
      f'{frame}<svg x="{x}" y="{y}" width="{emblem_size}" height="{emblem_size}" viewBox="0 0 1000 1000">'
      f'{inner_content(master)}</svg>'
      f'<text x="{title_x}" y="{title_y}" font-family="DejaVu Sans,Arial,sans-serif" font-style="italic" font-weight="900" font-size="{font_size}" letter-spacing="-5" fill="#FFFFFF">Power</text>'
      f'<text x="{link_x}" y="{title_y}" font-family="DejaVu Sans,Arial,sans-serif" font-style="italic" font-weight="900" font-size="{font_size}" letter-spacing="-5" fill="#09C6FF">Link</text>'
      f'{caption}</svg>\n')


def render(svg: str, size: tuple[int, int]) -> bytes:
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=size[0], output_height=size[1])


def png_bytes(svg: str, size: tuple[int, int], master_digest: str) -> bytes:
    from PIL import Image, PngImagePlugin
    png = render(svg, size)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("PowerLink-Approved-Master-SHA256", master_digest)
    output = io.BytesIO()
    # Lossless optimized DEFLATE. Crucially, never quantize gradients or drop pixels.
    image.save(output, format="PNG", optimize=True, compress_level=9, pnginfo=metadata)
    return output.getvalue()


def verify_png(path: Path, size: tuple[int, int], master_digest: str) -> None:
    from PIL import Image
    if not path.is_file():
        raise RuntimeError(f"Required brand image missing: {path}")
    with Image.open(path) as image:
        if image.size != size or image.mode != "RGBA":
            raise AssertionError(f"Unexpected dimensions/mode: {path} {image.size} {image.mode}")
        if image.info.get("PowerLink-Approved-Master-SHA256") != master_digest:
            raise AssertionError(f"Image was not generated from approved SVG: {path}")
        image.verify()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="check tracked outputs; do not change them")
    args = parser.parse_args()
    master = approved_svg()
    digest = hashlib.sha256(master).hexdigest()
    app = app_icon(master)
    banner = lockup(master, banner=True)
    logo = lockup(master, banner=False)
    # Every additional SVG is a compositional use of the approved master, not a variant.
    svg_outputs = {
        BRAND / "powerlink-app-icon.svg": app,
        BRAND / "powerlink-banner.svg": banner,
        BRAND / "powerlink-lockup.svg": logo,
    }
    renders = {
        "icon.png": (app, (256, 256)),
        "icon@2x.png": (app, (512, 512)),
        "logo.png": (logo, (432, 128)),
        "logo@2x.png": (logo, (864, 256)),
    }
    for path, content in svg_outputs.items():
        ET.fromstring(content.encode("utf-8"))
        if args.verify:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                import difflib
                old = path.read_text(encoding="utf-8") if path.is_file() else ""
                diff = "".join(difflib.unified_diff(old.splitlines(keepends=True),
                    content.splitlines(keepends=True), fromfile="committed",
                    tofile="generated"))
                print("Derived SVG mismatch:\n" + diff[:6000], file=sys.stderr)
                raise AssertionError(f"Derived SVG is not current: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if not args.verify:
        INTEGRATION_BRAND.mkdir(parents=True, exist_ok=True)
    for name, (svg, size) in renders.items():
        dest = INTEGRATION_BRAND / name
        if not args.verify:
            dest.write_bytes(png_bytes(svg, size, digest))
        verify_png(dest, size, digest)
        print(f"{name}: {size[0]}×{size[1]}, {dest.stat().st_size:,} bytes")
    # No duplicate dark theme or simplified small icons; Home Assistant supports
    # falling back to the normal images for dark-icon and dark-logo requests.
    if args.verify:
        obsolete = ("dark_icon.png", "dark_icon@2x.png", "dark_logo.png", "dark_logo@2x.png")
        if any((INTEGRATION_BRAND / filename).exists() for filename in obsolete):
            raise AssertionError("Obsolete alternate dark assets found; use one approved icon everywhere")
    print("Approved SVG source hash:", digest)


if __name__ == "__main__":
    main()
