# Changelog

## 2026-04-05 (3)

### Fix HTML export when installed via pipx
- Converted `poe_scrape.py` from a single-file module to a proper package (`poe_scrape/`)
- Templates are now declared as `package-data` and correctly included in the installed wheel
- `Path(__file__).parent / "templates"` resolution now works in all install contexts (pipx, pip, venv)

## 2026-04-05 (2)

### Source links now hyperlinked
- "Learn more" source URLs are now preserved from Poe's HTML across all three export formats (HTML, JSON, Markdown)
- HTML sources render as clickable `<a>` tags opening in new tabs
- JSON and Markdown sources use `[text](url)` format
- Stripped the trailing `---` divider that Poe inserts before "Learn more" (was causing a double divider in HTML)
- Source section font size normalized to match body text in all templates

### Thinking toggle restyled to match Poe
- Replaced the pill-shaped button with bold "Thinking >" text and chevron in default, light, and harajuku templates

### Header link opens in new tab
- The source URL in the sticky header now uses `target="_blank"` across all four templates

### `--no-header` flag
- New `--no-header` CLI flag omits the sticky header bar from HTML output
- Useful for embedding conversations in iframes or articles
- Supported across all four templates

### Terminal HTML template
- New `terminal` template: monospace green-on-dark CRT aesthetic inspired by The Matrix
- No bubbles — flat left-aligned messages with a `❯` prompt prefix for user input
- Bot name shown as a colored label with colon (`bot_name:`)
- ASCII `[+]/[-]` toggles for thinking blocks instead of pill buttons
- IRC-style bracketed timestamps `[2:34 PM]` and datestamps `[Date: Mar 15]`
- Uniform 14px monospace throughout — no font size variation
- Static block cursor in the header
- All green palette: `#33ff33` primary, phosphor-tinted accents throughout
- Dark gray background (`#141414`) for reduced contrast
- Pure CSS — no changes to the Python rendering pipeline

## 2026-04-05

### Bot name extraction
- Replaced fragile regex/CSS-selector strategies with a direct read of the `BotHeader_textContainer` DOM element, which is reliably present on every bot message wrapper

### Multi-bot support
- Each bot message now carries its own `sender_name` extracted from the DOM
- All format outputs (MD, JSON, HTML) use the per-message name as the author
- The top-level `bot_name` field shows `"Bot1 + Bot2"` when multiple bots are present; the HTML header avatar shows `+`

### `reasoning` → `thinking`
- Renamed the field everywhere to align with Anthropic/Google naming conventions

### Thinking block rendering
- Switched from code-block style (monospace, dark background) to blockquote style (left border, normal font) in all three templates
- Content is now rendered as Markdown instead of escaped plain text

### Blockquote color consistency
- Bot bubble blockquotes now use `var(--text-body)` (same as normal text) in harajuku and light templates
- User bubble blockquotes use `color: inherit` via a higher-specificity rule to override the `.bot-body blockquote` color
