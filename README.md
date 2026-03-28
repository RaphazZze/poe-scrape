# poe-scrape

A CLI tool to scrape [Poe.com](https://poe.com) shared conversations and export them
as Markdown, JSON, or self-contained HTML with a themeable chat UI.

No AI/LLM calls — purely programmatic Playwright DOM scraping.

---

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Usage

```
python poe_scrape.py [OPTIONS] URLS...
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-f`, `--format` | `md` | Output format: `md`, `json`, `html` |
| `-o`, `--output` | `poe_YYYY-MM-DD` | Output filename stem (no extension) |
| `--user` | `User` | Display name for the human speaker |
| `--bot-name` | *(scraped from page)* | Override the bot display name |
| `--time` | `12h` | Timestamp format: `12h` or `24h` |
| `--no-thoughts` | off | Exclude AI reasoning traces |
| `--no-sources` | off | Exclude "Learn more" source links from output |
| `--template` | `default` | HTML template name (in `templates/`) or path to a `.html` file. Only used with `-f html` |

---

## Examples

**Basic Markdown export:**
```bash
python poe_scrape.py https://poe.com/s/eaRlHYDcMzrgKUOQ66xR
# → poe_2026-03-14.md
```

**HTML export with a custom filename:**
```bash
python poe_scrape.py https://poe.com/s/abc123 -f html -o my_chat
# → my_chat.html
```

**HTML export with the light theme:**
```bash
python poe_scrape.py https://poe.com/s/abc123 -f html --template light
```

**Batch export to JSON, without reasoning traces:**
```bash
python poe_scrape.py https://poe.com/s/abc https://poe.com/s/def \
  -f json --no-thoughts -o session
# → session_1.json, session_2.json
```

**24-hour timestamps, custom speaker names:**
```bash
python poe_scrape.py https://poe.com/s/abc \
  --user "Raph" --bot-name "NISHA" --time 24h
```

**Convert a JSON export to HTML:**
```bash
python poe_scrape.py my_convo.json -f html
# → my_convo.html (default dark template)
```

**Reconvert a JSON export with a different template:**
```bash
python poe_scrape.py my_convo.json -f html --template harajuku
```

**Batch reconvert all JSON exports:**
```bash
python poe_scrape.py *.json -f html --template light
```

---

## Output Formats

**Markdown (`.md`)** — Clean, readable format. Date separators rendered as `🗓 **Mar 10**` with a horizontal rule. Reasoning traces rendered as blockquotes. Image attachments shown as `📎 filename` references. Works great in Obsidian, Notion imports, or plain text editors.

**JSON (`.json`)** — Fully structured data. Regular messages have fields: `role`, `author`, `timestamp`, `content`, `reasoning`, `sources`, `images`. Date separators appear inline as `{"type": "date", "label": "Mar 10"}`. Useful for further processing or archiving.

**HTML (`.html`)** — Self-contained file with no external dependencies. Themeable chat UI:
- User messages: right-aligned bubbles
- Bot messages: left-aligned bubbles with avatar and name above
- Date separators: centered pill dividers between message groups
- Reasoning traces: collapsible "thoughts" block styled like a code block
- All Markdown rendered: headings, bold, italic, strikethrough, lists, nested lists, task list checkboxes, tables, blockquotes, inline code, fenced code blocks, horizontal rules, footnotes, hyperlinks
- Horizontal scrolling on wide tables
- Syntax highlighting in code blocks via Pygments
- Copy-to-clipboard button on every code block
- Image attachments shown as filename chips
- All links open in a new tab

---

## HTML Templates

Templates are `.html` files in the `templates/` directory using [Jinja2](https://jinja.palletsprojects.com/) syntax. Three templates are included:

| Name | Description |
|------|-------------|
| `default` | Dark UI styled after Poe. Monokai syntax highlighting. |
| `light` | Light UI with white page, gray bubbles. Friendly syntax highlighting. |
| `harajuku` | Dark UI with a hot pink & purple palette. Dracula syntax highlighting. |

**Using a built-in template:**
```bash
python poe_scrape.py https://poe.com/s/abc123 -f html --template harajuku
```

**Using a custom template file:**
```bash
python poe_scrape.py https://poe.com/s/abc123 -f html --template /path/to/my_theme.html
```

### Creating a custom template

Copy any existing template from `templates/` and edit it. The following variables are available:

| Variable | Description |
|----------|-------------|
| `{{ bot_name }}` | Bot display name (HTML-escaped) |
| `{{ bot_initial }}` | First letter of the bot name |
| `{{ url }}` | Source conversation URL (HTML-escaped) |
| `{{ messages }}` | Pre-rendered HTML of all message blocks |
| `{{ pygments_css }}` | Pygments syntax highlighting CSS (optional) |
| `{{ js }}` | Built-in copy-to-clipboard JavaScript |

Templates are fully self-contained — open them directly in a browser to preview the UI with placeholder bubbles (shown when `{{ messages }}` is empty).

---

## Notes

- Only **shared conversation links** (`poe.com/s/...`) are supported. Private chat URLs will not work.
- The scraper waits 6 seconds after page load for Poe's SPA to finish rendering. On slow connections this may need to be increased (edit `wait_for_timeout(6000)` in `scrape_url`).
- Bot name is detected from the page body. Use `--bot-name` to override if detection is incorrect.
- Emojis and all Unicode characters are preserved throughout (UTF-8 output).
- Three reasoning trace formats are supported: the "thoughts" code block style, the extended thinking blockquote style, and the MarkdownThinkingBlock toggle UI.
- JSON exports can be reconverted to any other format at any time by passing the `.json` file as input instead of a URL. Image attachments are stored as filename references only — original URLs point to Poe's CDN and are not accessible outside Poe.
