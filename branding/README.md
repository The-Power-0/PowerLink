# PowerLink brand guidelines (v1.0.0 candidate)

PowerLink is the Home Assistant companion to PowerPlay. Their geometry and accent colors are related but the glyphs are intentionally distinct.

- **Primary glyph:** `branding/powerlink-glyph.svg` — white left link, blue right link, central cyan lightning bolt.
- **Light-theme glyph:** `branding/powerlink-glyph-light.svg` — dark left link for visibility on white.
- **App tile:** `branding/powerlink-app-icon.svg` — navy rounded square with narrow cyan/blue border.
- **Wordmark:** `branding/powerlink-banner.svg` — white POWER and blue LINK; supplemental descriptor may be omitted.
- **Monochrome:** `branding/powerlink-monochrome.svg` for tiny toolbars and accessibility contexts.

Palette: navy #070B23, cyan #00F2F0, blue #1168FF, white #FFFFFF. Use restrained glow only in marketing art; functional icons must work without it.

The eight Home Assistant PNG assets in `custom_components/powerlink/brand/` are generated from these masters by `python tools/render_brand.py`. Do not edit generated PNGs by hand. App identity and API domain remain unchanged.
