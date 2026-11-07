# Transparent Trading — display brand

Display name: **Transparent Trading** (two words, title case).

This is the public-facing name for the desk chat and future retail screens. Internal code, services, routes, ledgers, and seat ids stay THF (`/desk/chat`, `tfo-thf-tick`, seat names). Renaming those is not part of this brand pass.

The look is restrained financial-services typography: deep navy, paper background, one teal accent, generous space. It is an original mark. It does not use another bank's wordmark, logo, octagon, or trade dress, and it does not say the product is affiliated with, endorsed by, or certified by any bank.

Trademark and domain availability were **not** checked. Nothing here registers, buys, or publishes a name.

## Mark

- Source: `assets/brand/mark.svg` (monochrome `currentColor`, readable at 16px).
- Favicon: `assets/brand/favicon.svg`.
- Wordmark: `assets/brand/wordmark.svg`.
- The mark is a rounded window with a T-stroke. Clear space: at least the stroke width on every side. Do not rotate, recolor with a profit-green, or add a badge.

## Color

Tokens live in `assets/brand/tokens.css`.

| Token | Light | Use |
|---|---|---|
| Navy `#10243E` | Header | White text `#F4F7F6` on navy |
| Ink `#142033` | Body text | On paper `#F4F7F6` |
| Muted `#3E5160` | Secondary text | On paper, not as the only status signal |
| Teal `#1F4E5F` | Links and focus | Not a "winning" color |
| Danger `#8C2F2F` | Errors | |
| Warning `#8A5A12` | Caution | |
| Ok `#1D5C38` | A checked fact only | Never a decorative trust badge |

Dark theme swaps paper and ink. See `data-theme="dark"` in the token file. Status chips still take their color from the feed record, not from the brand.

## Type

UI face: Inter, then Noto Sans, then Segoe UI, then a generic sans. Inter and Noto Sans are SIL Open Font License faces already present on the build machine. Pages do not fetch a webfont from a third party. If those faces are missing, the generic sans is the fallback. Do not substitute a bank's proprietary face.

## Voice

Say what the screen is doing: paper trading, loading, could not load, model used unknown. Do not say guaranteed profit, casino language, or "institutional certified."

## Where it is applied

`tools/thf_chat_page.html` and `tools/thf_chat.js` (the `/desk/chat` renderer). Screenshots in this change are from a local fixture with mocked APIs, not from the Singapore tailnet.
