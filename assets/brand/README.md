# PowerLink brand assets — v1.0.2

The **only** approved mark is `powerlink-mark.svg` (SHA-256 `d7184cc8df4d35311fb68c6eaff89632d30b570594b39e20f73754f4ab60e200`). It contains the exact PowerPlay v10 seven-vertex bolt, the blue upper-right-to-silver lower-left gradient, two parallel-cut capsule links, and mathematically equal gap and band measurements.

There is **no small, flat, monochrome, or alternate dark variant**. Use this full-resolution vector everywhere SVG is supported:
- `powerlink-mark.svg` — canonical transparent glyph, used without modification.
- `powerlink-app-icon.svg` — the same mark inside the approved navy icon tile.
- `powerlink-banner.svg` / `powerlink-lockup.svg` — compositional uses of the canonical SVG with a wordmark; no alternate mark is drawn.
- `custom_components/powerlink/brand/icon*.png` and `logo*.png` — required Home Assistant PNG assets, rendered directly at native target sizes from the SVG with lossless RGBA compression.

Home Assistant uses the normal icon/logo as a fallback for dark mode, so no duplicate dark assets are needed. The integrated artwork may lose tiny details when displayed at 256px; this is intentional, not an invitation to simplify the icon.

Rebuild: `python -m pip install Pillow==12.3.0 CairoSVG==2.8.2` then `python scripts/branding/render_powerlink.py`.
Validate: `python scripts/branding/render_powerlink.py --verify`.
No additional Python dependencies are required for running PowerLink in Home Assistant; the renderer is a release-time development tool only.

PowerLink is independent of Home Assistant branding and is a companion to PowerPlay.
