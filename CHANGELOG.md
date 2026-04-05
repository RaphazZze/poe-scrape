# Changelog

## 2026-04-05 (2)

### `--no-header` flag
- New `--no-header` CLI flag omits the sticky header bar from HTML output
- Useful for embedding conversations in iframes or articles
- Supported across all four templates

### Terminal HTML template
- New `terminal` template: monospace green-on-black CRT aesthetic inspired by The Matrix
- No bubbles — flat left-aligned messages with a `❯` prompt prefix for user input
- Bot name shown as a colored label with colon (`bot_name:`)
- ASCII `[+]/[-]` toggles for thinking blocks instead of pill buttons
- Date separators as `Date: Mar 15` labels
- Static block cursor in the header
- All green palette: `#33ff33` primary, phosphor-tinted accents throughout
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
