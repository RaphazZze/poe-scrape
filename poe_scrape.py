"""poe_scrape.py — CLI tool to scrape and export Poe.com shared conversations."""

import asyncio
import html as html_lib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import click
import jinja2
import markdown as md_lib
from markdownify import markdownify as html_to_md
from playwright.async_api import async_playwright
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter as PygHtmlFormatter
from pygments.lexers import get_lexer_by_name as pyg_get_lexer
from pygments.util import ClassNotFound as PygClassNotFound

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Generate Pygments CSS once (monokai theme, scoped to .highlight)
_PYGMENTS_CSS = PygHtmlFormatter(style="monokai", nowrap=True).get_style_defs(".highlight")


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> None:
    """Warn if the URL doesn't look like a Poe shared conversation link."""
    if not re.match(r'https?://(www\.)?poe\.com/s/', url):
        click.echo(
            f"  Warning: '{url}' doesn't look like a Poe shared conversation link "
            f"(expected https://poe.com/s/...). Scraping anyway, but it may fail.",
            err=True,
        )


async def scrape_url(url: str) -> dict:
    """Fetch a Poe shared conversation page and return raw message data."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="load", timeout=30000)
                await page.wait_for_timeout(6000)
                await page.wait_for_selector('[class*="Message_row"]', timeout=5000)
            except Exception:
                pass  # Continue even if selector never appears

            # Expand any auto-collapsed "Thinking" reasoning sections before extraction.
            # Poe (Format C) uses a MarkdownThinkingBlock with a toggle button; the blockquote
            # content is in the DOM but may need the button clicked to be fully rendered.
            try:
                await page.evaluate("""() => {
                    document.querySelectorAll('[class*="MarkdownThinkingBlock_header"]').forEach(btn => btn.click());
                }""")
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            result = await page.evaluate("""() => {
            // ── helpers ──────────────────────────────────────────────────────
            // Extract reasoning text from a blockquote, preserving paragraph breaks.
            // bq.innerText collapses <p> separators; join with double newlines instead.
            function bqToText(bq) {
                const ps = bq.querySelectorAll('p');
                if (ps.length > 0)
                    return Array.from(ps).map(p => p.innerText.trim()).filter(t => t).join('\\n\\n');
                return bq.innerText.trim();
            }

            // Extract structured data from a single Message_row element.
            // Three reasoning trace formats exist in Poe:
            //
            // Format A — NSH-style: reasoning is a MarkdownCodeBlock_container
            //   with language label "thoughts". Strip it.
            //
            // Format B — "Thinking..." (extended thinking models): reasoning is a
            //   <blockquote> immediately following a <p>Thinking...</p>. Extract the
            //   blockquote text, then remove both elements from the clone.
            //
            // Format C — MarkdownThinkingBlock (new Poe UI): a div[MarkdownThinkingBlock_root]
            //   inside markdownContainer wraps a toggle button and a
            //   <blockquote[MarkdownThinkingBlock_content]>. Extract the blockquote,
            //   then remove the whole block from the clone.
            //
            // Also normalise all remaining MarkdownCodeBlock_containers into standard
            // <pre><code> elements so markdownify can produce proper fenced blocks.
            function extractMessage(el) {
                const mainMdEl = el.querySelector('[class*="markdownContainer"]');
                let contentHtml = null;
                let thinkingText = null;

                if (mainMdEl) {
                    const clone = mainMdEl.cloneNode(true);

                    // Format B: detect <p>Thinking...</p> + <blockquote>
                    const prose = clone.querySelector('[class*="Prose_prose"]');
                    if (prose) {
                        const firstEl = prose.firstElementChild;
                        if (firstEl && firstEl.tagName === 'P' &&
                            /^Thinking\\.{0,3}$/.test(firstEl.innerText.trim())) {
                            firstEl.remove();
                            const bq = prose.firstElementChild;
                            if (bq && bq.tagName === 'BLOCKQUOTE') {
                                thinkingText = bqToText(bq);
                                bq.remove();
                            }
                            // No blockquote = label-only format; "Thinking" removed, thinkingText stays null
                        }
                    }

                    // Format C: MarkdownThinkingBlock (new Poe auto-collapsed UI).
                    // A div[class*="MarkdownThinkingBlock_root"] wraps a toggle button and a
                    // <blockquote class*="MarkdownThinkingBlock_content"> — all inside markdownContainer.
                    if (!thinkingText) {
                        const thinkingBlock = clone.querySelector('[class*="MarkdownThinkingBlock_root"]');
                        if (thinkingBlock) {
                            const bq = thinkingBlock.querySelector('blockquote');
                            if (bq) thinkingText = bqToText(bq);
                            thinkingBlock.remove();
                        }
                    }

                    // Format A (variant): NSH-style bots always put their thinking block first.
                    // Extract it regardless of language label for retro-compatibility.
                    if (!thinkingText) {
                        const firstBlock = clone.querySelector('[class*="MarkdownCodeBlock_container"]');
                        if (firstBlock && firstBlock === clone.firstElementChild) {
                            const preTag = firstBlock.querySelector('[class*="preTag"]');
                            if (preTag) {
                                thinkingText = preTag.innerText.trim();
                                firstBlock.remove();
                            }
                        }
                    }

                    // Format A + normalise remaining code blocks
                    clone.querySelectorAll('[class*="MarkdownCodeBlock_container"]').forEach(block => {
                        const langLabel = block.querySelector('[class*="languageName"]');
                        const lang = langLabel ? langLabel.innerText.trim() : '';
                        if (lang.toLowerCase() === 'thoughts') {
                            block.remove();
                            return;
                        }
                        // Replace with standard <pre><code class="language-xxx">
                        const preTag = block.querySelector('[class*="preTag"]');
                        const codeText = preTag ? preTag.innerText : '';
                        const pre = document.createElement('pre');
                        const code = document.createElement('code');
                        if (lang) code.setAttribute('class', 'language-' + lang);
                        code.textContent = codeText;
                        pre.appendChild(code);
                        block.parentNode.replaceChild(pre, block);
                    });

                    // Replace checkbox inputs with text markers (markdownify strips <input>)
                    clone.querySelectorAll('input[type="checkbox"]').forEach(inp => {
                        const marker = document.createTextNode(inp.checked ? 'CBXON\u200b ' : 'CBXOFF\u200b ');
                        inp.parentNode.insertBefore(marker, inp);
                        inp.remove();
                    });

                    contentHtml = clone.innerHTML;
                }

                // Capture attached images (user uploads live outside markdownContainer)
                const images = [];
                el.querySelectorAll('[class*="Attachments"] img, [class*="attachment"] img').forEach(img => {
                    const src = img.src || img.getAttribute('src') || '';
                    const alt = img.alt || img.getAttribute('alt') || '';
                    if (src && !src.startsWith('data:')) images.push({ src, alt });
                });

                const isHuman = Array.from(el.classList).some(c => /right/i.test(c));
                return { itemType: 'message', text: el.innerText.trim(), contentHtml, thinkingText, images, isHuman };
            }

            // ── main: walk tupleGroupContainer elements in DOM order ─────────────
            // Poe groups messages by date inside ShareMessageList_tupleGroupContainer
            // divs. Each group starts with a MessageDate_container pill (the date label),
            // followed by ShareMessage_wrapper elements for each message turn.
            // Each ShareMessage_wrapper for bot messages contains a BotHeader element
            // with the sender's name — extract it per message for multi-bot support.
            const groups = document.querySelectorAll('[class*="tupleGroupContainer"]');
            const items = [];

            for (const group of Array.from(groups)) {
                // Date label — the pill at the top of each date group
                const datePill = group.querySelector('[class*="MessageDate_container"]');
                if (datePill) {
                    const label = (datePill.innerText || '').trim();
                    if (label) items.push({ itemType: 'date', label });
                }

                // Messages — direct ShareMessage_wrapper children of this group
                for (const child of Array.from(group.children)) {
                    const childClasses = Array.from(child.classList).join(' ');
                    if (!/ShareMessage_wrapper/i.test(childClasses)) continue;
                    const row = child.querySelector('[class*="Message_row"]:not([class*="WithFooter"])');
                    if (row) {
                        const msg = extractMessage(row);
                        // Extract per-message sender name for bot messages from BotHeader
                        if (!msg.isHuman) {
                            const nameEl = child.querySelector('[class*="BotHeader_textContainer"] p');
                            if (nameEl) msg.senderName = nameEl.innerText.trim();
                        }
                        items.push(msg);
                    }
                }
            }

            // Fallback: if tupleGroupContainer wasn't found, collect messages without dates
            if (items.length === 0) {
                document.querySelectorAll('[class*="Message_row"]:not([class*="WithFooter"])').forEach(row => {
                    items.push(extractMessage(row));
                });
            }

            // ── bot name ──────────────────────────────────────────────────────
            // Derive from the first bot message's senderName (most reliable source).
            let botName = null;
            for (const item of items) {
                if (item.itemType === 'message' && !item.isHuman && item.senderName) {
                    botName = item.senderName;
                    break;
                }
            }

            return { items, botName: botName || 'Bot' };
        }""")

            return result
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

TIMESTAMP_RE = re.compile(r'\d{1,2}:\d{2}\s*(?:AM|PM)?$', re.IGNORECASE)
# Matches the "Learn more:" block and everything after it
SOURCES_RE = re.compile(r'\n{1,}Learn more:\n+([\s\S]+)$', re.IGNORECASE)


def _extract_timestamp(text: str) -> tuple[str, str | None]:
    """Strip trailing timestamp from text, return (cleaned_text, timestamp_str)."""
    lines = text.rstrip().splitlines()
    if lines and TIMESTAMP_RE.match(lines[-1].strip()):
        ts = lines[-1].strip()
        cleaned = "\n".join(lines[:-1]).rstrip()
        return cleaned, ts
    return text, None


def _extract_sources(text: str) -> tuple[str, list[str]]:
    """Strip 'Learn more:' block from text, return (cleaned_text, source_lines)."""
    match = SOURCES_RE.search(text)
    if match:
        sources = [line.strip() for line in match.group(1).splitlines() if line.strip()]
        cleaned = text[:match.start()].rstrip()
        return cleaned, sources
    return text, []


def _extract_thoughts(text: str) -> tuple[str, str | None]:
    """
    If text starts with 'thoughts\\n', split on first double-newline.
    Returns (content, thoughts_text) or (text, None).
    """
    if text.startswith("thoughts\n"):
        body = text[len("thoughts\n"):]
        parts = body.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip(), parts[0].strip()
        return "", body.strip()
    return text, None


_LEADING_FENCED_THOUGHTS_RE = re.compile(
    r'^```[^\n]*\n(.*?)\n```[ \t]*\n(.*)',
    re.DOTALL,
)


_PRE_CODE_RE = re.compile(
    r'<pre><code(?:\s+class="(?:language-)?([^"]*)")?>(.*?)</code></pre>',
    re.DOTALL,
)


def _to_markdown(content_html: str | None, fallback_text: str) -> str:
    """Convert innerHTML to Markdown if available, otherwise use plain text."""
    if content_html:
        # Extract code blocks before markdownify — it silently drops language labels
        code_blocks: list[tuple[str, str]] = []
        def _stash_code(m: re.Match) -> str:
            lang = m.group(1) or ""
            code = html_lib.unescape(m.group(2))
            code_blocks.append((lang, code))
            return f"CODEBLOCK{len(code_blocks) - 1}ENDCODE"

        html_in = _PRE_CODE_RE.sub(_stash_code, content_html)
        # Preserve <sup> elements — markdownify strips the tags, leaving bare numbers
        html_in = re.sub(r'<sup>(.*?)</sup>', r'SUPOPEN\1SUPCLOSE', html_in, flags=re.DOTALL)

        converted = html_to_md(
            html_in,
            heading_style="ATX",
            bullets="-",
        ).strip()

        # Restore superscripts
        converted = re.sub(r'SUPOPEN(.*?)SUPCLOSE', r'<sup>\1</sup>', converted, flags=re.DOTALL)
        # Restore code blocks as fenced markdown with language label
        for i, (lang, code) in enumerate(code_blocks):
            fence = f"```{lang}\n{code}\n```"
            converted = converted.replace(f"CODEBLOCK{i}ENDCODE", fence)

        if converted:
            return converted
    return fallback_text.strip()


def parse_messages(raw_items: list[dict], opts: dict) -> list[dict]:
    """
    Process raw items from the scraper (messages + date separators) into a mixed list
    of parsed message dicts and date-event dicts.

    Date events pass through as {"type": "date", "label": "..."}.
    Messages are deduplicated, have roles assigned, and have structured fields extracted.

    opts keys: no_thoughts (bool), no_sources (bool), time_fmt (str)
    """
    # First pass: deduplicate messages by text content (first occurrence wins).
    # Date items are not subject to dedup.
    seen: set[str] = set()
    keep_ids: set[int] = set()
    for item in raw_items:
        if item.get("itemType") != "message":
            continue
        key = item.get("text", "")
        if key and key not in seen:
            seen.add(key)
            keep_ids.add(id(item))

    result: list[dict] = []
    msg_idx = 0  # index among kept messages only — for fallback role assignment

    for item in raw_items:
        if item.get("itemType") == "date":
            result.append({"type": "date", "label": item["label"]})
            continue

        if id(item) not in keep_ids:
            continue

        # Use the isHuman flag scraped from the DOM class name.
        # Fall back to index parity for JSON imports that predate this field.
        if "isHuman" in item:
            role = "user" if item["isHuman"] else "bot"
        else:
            role = "user" if msg_idx % 2 == 0 else "bot"

        text = item.get("text", "")
        content_html = item.get("contentHtml")

        text, timestamp = _extract_timestamp(text)

        if role == "bot":
            text, sources = _extract_sources(text)
            thinking_text = item.get("thinkingText")
            if thinking_text is not None:
                # Format B: "Thinking..." — reasoning already extracted and removed
                # from contentHtml in JS; plain_content fallback is the full innerText
                thoughts = thinking_text
                plain_content = text.strip()
            else:
                # Format A: "thoughts\n" — extract reasoning from innerText
                plain_content, thoughts = _extract_thoughts(text)
            # Use HTML→Markdown for content (preserves headings, bold, etc.)
            content = _to_markdown(content_html, plain_content)
            # Strip sources from markdownified content too (they may appear as links)
            content, _ = _extract_sources(content)
            # Fallback: if no thinking was detected above but content starts with a
            # fenced ```thoughts``` or ```markdown``` block, extract it as reasoning.
            # This catches NSH-style bots where the JS heuristic couldn't fire
            # (e.g. the thinking block wasn't the direct first child in the DOM).
            if not thoughts:
                m = _LEADING_FENCED_THOUGHTS_RE.match(content)
                if m:
                    thoughts = m.group(1).strip()
                    content = m.group(2).strip()
        else:
            sources = []
            thoughts = None
            content = _to_markdown(content_html, text.strip())

        # Strip Poe UI artefacts that leak into innerText
        content = re.sub(r'\n*\bView more\b\n*', '', content).strip()
        # Strip bare "Thinking" / "Thinking..." label that leaks when Poe emits no blockquote
        if role == "bot":
            content = re.sub(r'^Thinking\.{0,3}\s*\n+', '', content).strip()

        result.append({
            "type": "message",
            "role": role,
            "sender_name": item.get("senderName") if role == "bot" else None,
            "content": content,
            "thinking": thoughts,
            "sources": sources,
            "timestamp": timestamp,
            "images": item.get("images") or [],
        })
        msg_idx += 1

    return result


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def format_timestamp(ts: str | None, time_fmt: str) -> str:
    if not ts:
        return ""
    if time_fmt == "12h":
        return ts
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(ts.upper().strip(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return ts  # Fallback: return raw if parsing fails


# ---------------------------------------------------------------------------
# Bot name helpers
# ---------------------------------------------------------------------------

def _merged_bot_name(messages: list[dict], fallback: str) -> str:
    """Return a display name reflecting all unique bots in the conversation.

    Collects bot sender_names in order of first appearance. If only one bot is
    present, returns that name. For multiple bots returns "Bot1 + Bot2 + ...".
    Falls back to *fallback* if no sender names are found.
    """
    seen: set[str] = set()
    names: list[str] = []
    for msg in messages:
        if msg.get("role") == "bot":
            name = msg.get("sender_name") or fallback
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    if not names:
        return fallback
    return " + ".join(names)


# ---------------------------------------------------------------------------
# Markdown exporter
# ---------------------------------------------------------------------------

def format_md(messages: list[dict], meta: dict, opts: dict) -> str:
    lines = [
        f"# Poe Conversation — {meta['bot_name']}",
        f"**Source:** {meta['url']}",
        "",
        "---",
        "",
    ]

    for item in messages:
        if item.get("type") == "date":
            lines.append(f"🗓 **{item['label']}**")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        msg = item
        author = meta["user_name"] if msg["role"] == "user" else (msg.get("sender_name") or meta["bot_name"])
        ts = format_timestamp(msg["timestamp"], opts["time_fmt"])
        lines.append(f"### ❯ {author}" + (f" · {ts}" if ts else ""))
        lines.append("")

        if msg.get("images"):
            for img in msg["images"]:
                label = img.get("alt") or img.get("src", "").split("/")[-1].split("?")[0] or "image"
                lines.append(f"📎 `{label}`")
            lines.append("")

        if msg["role"] == "bot" and msg.get("thinking") and not opts["no_thoughts"]:
            lines.append("> **Thinking...**")
            for thought_line in msg["thinking"].splitlines():
                lines.append(f"> {thought_line}" if thought_line.strip() else ">")
            lines.append("")

        lines.append(msg["content"])
        lines.append("")

        if msg["role"] == "bot" and msg.get("sources") and not opts["no_sources"]:
            lines.append("**Sources:**")
            for src in msg["sources"]:
                lines.append(f"- {src}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON exporter
# ---------------------------------------------------------------------------

def format_json(messages: list[dict], meta: dict, opts: dict) -> str:
    output_messages = []
    for item in messages:
        if item.get("type") == "date":
            output_messages.append({"type": "date", "label": item["label"]})
            continue
        msg = item
        output_messages.append({
            "role": msg["role"],
            "author": meta["user_name"] if msg["role"] == "user" else (msg.get("sender_name") or meta["bot_name"]),
            "timestamp": format_timestamp(msg["timestamp"], opts["time_fmt"]) or None,
            "thinking": None if opts["no_thoughts"] else msg.get("thinking"),
            "content": msg["content"],
            "sources": [] if opts["no_sources"] else msg.get("sources", []),
            "images": [img.get("alt") or img.get("src", "").split("/")[-1].split("?")[0] for img in msg.get("images", [])],
        })

    payload = {
        "url": meta["url"],
        "bot_name": meta["bot_name"],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "messages": output_messages,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# HTML exporter
# ---------------------------------------------------------------------------



_CODE_BLOCK_RE = re.compile(
    r'<pre><code(?:\s+class="(?:language-)?([^"]*)")?>(.*?)</code></pre>',
    re.DOTALL,
)


_COPY_BTN = (
    '<button class="code-copy-btn" onclick="copyCode(this)" title="Copy">'
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
    '</svg></button>'
)


def _wrap_code_blocks(rendered_html: str) -> str:
    """Wrap <pre><code class="language-X"> in a Poe-style header + body container with syntax highlighting."""
    def replace(m: re.Match) -> str:
        lang = m.group(1) or ""
        code_content = m.group(2)          # HTML-escaped by Python-Markdown
        raw_code = html_lib.unescape(code_content)
        label = html_lib.escape(lang) if lang else "code"

        # Syntax-highlight with Pygments if language is known
        try:
            lexer = pyg_get_lexer(lang, stripall=False) if lang else None
        except PygClassNotFound:
            lexer = None

        if lexer:
            formatter = PygHtmlFormatter(style="monokai", nowrap=True)
            highlighted = pyg_highlight(raw_code, lexer, formatter)
            code_inner = f'<pre class="highlight"><code>{highlighted}</code></pre>'
        else:
            code_inner = f'<pre><code>{code_content}</code></pre>'

        return (
            f'<div class="code-block">'
            f'<div class="code-block-header"><span>{label}</span>{_COPY_BTN}</div>'
            f'{code_inner}'
            f'</div>'
        )
    return _CODE_BLOCK_RE.sub(replace, rendered_html)


_JS = """\
function copyCode(btn) {
  var code = btn.closest('.code-block').querySelector('code');
  navigator.clipboard.writeText(code.innerText).then(function() {
    var toast = document.getElementById('copy-toast');
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 2000);
  });
}

(function() {
  var THRESHOLD = 1000;

  function makeBtn() {
    var btn = document.createElement('button');
    btn.className = 'view-more-btn';
    btn.textContent = 'View more';
    return btn;
  }

  function collapse(body, btn, insertTarget) {
    if (body.scrollHeight <= THRESHOLD) return;
    body.classList.add('bubble-collapsed');
    insertTarget.parentNode.insertBefore(btn, insertTarget);
    btn.onclick = function() {
      body.classList.remove('bubble-collapsed');
      btn.remove();
    };
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.user-bubble').forEach(function(bubble) {
      var body = bubble.querySelector('.bubble-body');
      var ts   = bubble.querySelector('.ts');
      if (!body) return;
      var btn = makeBtn();
      collapse(body, btn, ts || body.nextSibling || bubble.appendChild(document.createTextNode('')));
      if (!ts) bubble.appendChild(btn);
    });

  });
})();"""


def _resolve_template(name: str) -> str:
    """Return the Jinja2 template source for the given name or path."""
    p = Path(name)
    if not p.suffix:
        p = _TEMPLATES_DIR / f"{name}.html"
    if not p.is_file():
        raise click.BadParameter(f"Template not found: {p}", param_hint="'--template'")
    return p.read_text(encoding="utf-8")


def format_html(messages: list[dict], meta: dict, opts: dict, template: str = "default") -> str:
    md_extensions = ["fenced_code", "tables", "footnotes"]

    def render(text: str) -> str:
        t = text or ""
        # Strikethrough: ~~text~~ → <del>
        t = re.sub(r'~~(.+?)~~', r'<del>\1</del>', t, flags=re.DOTALL)
        # Checkbox markers injected by JS scraper
        t = t.replace('CBXON\u200b ', '<span class="cb cb-on">✓</span> ')
        t = t.replace('CBXOFF\u200b ', '<span class="cb cb-off"></span> ')
        raw = md_lib.markdown(t, extensions=md_extensions, tab_length=2)
        raw = _wrap_code_blocks(raw)
        raw = re.sub(r'<table>', '<div class="table-wrap"><table>', raw)
        raw = re.sub(r'</table>', '</table></div>', raw)
        raw = re.sub(r'<a\s+href=', '<a target="_blank" rel="noopener noreferrer" href=', raw)
        return raw

    # Avatar: "+" for multi-bot conversations, first char otherwise
    _is_multi_bot = " + " in meta["bot_name"]
    bot_initial = html_lib.escape("+" if _is_multi_bot else (meta["bot_name"][0].upper() if meta["bot_name"] else "B"))
    bot_name_safe = html_lib.escape(meta["bot_name"])
    url_safe = html_lib.escape(meta["url"])

    msg_blocks = []
    for item in messages:
        if item.get("type") == "date":
            msg_blocks.append(
                f'<div class="date-sep"><span>{html_lib.escape(item["label"])}</span></div>\n'
            )
            continue

        msg = item
        role = msg["role"]
        ts = format_timestamp(msg["timestamp"], opts["time_fmt"])

        if role == "user":
            ts_html = f'<span class="ts">{html_lib.escape(ts)}</span>' if ts else ""
            images = msg.get("images") or []
            imgs_html = ""
            if images:
                chips = "".join(
                    f'<div class="img-chip">'
                    f'<span class="img-chip-icon">🖼</span>'
                    f'<span class="img-chip-name">{html_lib.escape(img["alt"] or img["src"].split("/")[-1].split("?")[0] or "image")}</span>'
                    f'</div>'
                    for img in images
                )
                imgs_html = f'<div class="img-attachments">{chips}</div>'
            content_html = render(msg["content"])
            msg_blocks.append(
                f'<div class="msg msg-user">\n'
                f'  <div class="user-bubble">'
                f'<div class="bubble-body bot-body">{imgs_html}{content_html}</div>'
                f'{ts_html}</div>\n'
                f'</div>\n'
            )
        else:
            ts_html = (
                f'<span class="bot-ts-bottom">{html_lib.escape(ts)}</span>' if ts else ""
            )

            body_parts = []

            if msg.get("thinking") and not opts["no_thoughts"]:
                body_parts.append(
                    f'<details class="thoughts">'
                    f'<summary>'
                    f'<span>Thinking</span>'
                    f'<svg class="thoughts-chevron" xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" viewBox="0 0 24 24">'
                    f'<path fill="currentColor" d="M9.29 6.71a.996.996 0 0 0 0 1.41L13.17 12l-3.88 3.88a.996.996 0 1 0 1.41 1.41l4.59-4.59a.996.996 0 0 0 0-1.41L10.7 6.71a.996.996 0 0 0-1.41 0z"/>'
                    f'</svg>'
                    f'</summary>'
                    f'<div class="thoughts-body">{render(msg["thinking"])}</div>'
                    f'</details>'
                )

            body_parts.append(render(msg["content"]))

            if msg.get("sources") and not opts["no_sources"]:
                items = "".join(
                    f'<li>{html_lib.escape(src)}</li>' for src in msg["sources"]
                )
                body_parts.append(
                    f'<div class="sources"><strong>Learn more</strong><ul>{items}</ul></div>'
                )

            msg_sender = html_lib.escape(msg.get("sender_name") or meta["bot_name"])
            msg_initial = html_lib.escape((msg.get("sender_name") or meta["bot_name"])[0].upper())
            msg_blocks.append(
                f'<div class="msg msg-bot">\n'
                f'  <div class="bot-wrapper">\n'
                f'    <div class="bot-label-row">'
                f'<div class="avatar">{msg_initial}</div>'
                f'<span class="bot-name">{msg_sender}</span>'
                f'</div>\n'
                f'    <div class="bot-content">\n'
                f'      <div class="bot-body">{"".join(body_parts)}</div>\n'
                f'      {ts_html}\n'
                f'    </div>\n'
                f'  </div>\n'
                f'</div>\n'
            )

    tmpl_src = _resolve_template(template)
    env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
    return env.from_string(tmpl_src).render(
        bot_name=bot_name_safe,
        bot_initial=bot_initial,
        url=url_safe,
        messages="".join(msg_blocks),
        pygments_css=_PYGMENTS_CSS,
        js=_JS,
        no_header=opts.get("no_header", False),
    )


# ---------------------------------------------------------------------------
# JSON import
# ---------------------------------------------------------------------------

def load_json_export(path: str, bot_name_override: str | None, user_name: str) -> tuple[list[dict], dict]:
    """Load a poe-scrape JSON export and return (messages, meta) ready for formatting."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in {path}: {e}")

    if "messages" not in data:
        raise click.ClickException(f"{path} doesn't look like a poe-scrape JSON export (missing 'messages' key).")

    # Infer user_name from the first user message's author if not overridden
    inferred_user = user_name
    for msg in data.get("messages", []):
        if msg.get("role") == "user":
            inferred_user = msg.get("author", user_name)
            break

    meta = {
        "url": data.get("url", ""),
        "bot_name": bot_name_override or data.get("bot_name", "Bot"),
        "user_name": inferred_user,
    }

    messages = []
    for msg in data.get("messages", []):
        if msg.get("type") == "date":
            messages.append({"type": "date", "label": msg.get("label", "")})
            continue
        # JSON export stores images as plain filename strings; reconstruct for the HTML renderer
        images = [{"src": "", "alt": img} for img in msg.get("images") or []]
        role = msg.get("role", "bot")
        # Per-message author stored in "author" field; use it as sender_name for bot messages
        sender_name = msg.get("author") if role == "bot" else None
        messages.append({
            "type": "message",
            "role": role,
            "sender_name": sender_name,
            "content": msg.get("content", ""),
            "thinking": msg.get("thinking"),
            "sources": msg.get("sources") or [],
            "timestamp": msg.get("timestamp"),
            "images": images,
        })

    if not bot_name_override:
        meta["bot_name"] = _merged_bot_name(messages, meta["bot_name"])

    return messages, meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EXT = {"md": ".md", "json": ".json", "html": ".html"}


@click.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--format", "-f", "fmt",
              type=click.Choice(["md", "json", "html"]), default="md",
              show_default=True, help="Output format.")
@click.option("--output", "-o", "output_stem", default=None,
              help="Output filename stem (no extension). Default: poe_YYYY-MM-DD.")
@click.option("--user", "user_name", default="User", show_default=True,
              help="Display name for the human speaker.")
@click.option("--bot-name", "bot_name_override", default=None,
              help="Override the bot display name (scraped from page by default).")
@click.option("--time", "time_fmt",
              type=click.Choice(["24h", "12h"]), default="12h",
              show_default=True, help="Timestamp format.")
@click.option("--no-thoughts", "no_thoughts", is_flag=True, default=False,
              help="Exclude AI reasoning traces from output.")
@click.option("--no-sources", "no_sources", is_flag=True, default=False,
              help="Exclude 'Learn more' source links from output.")
@click.option("--no-header", "no_header", is_flag=True, default=False,
              help="Omit the sticky header from HTML output (useful for iframe embedding).")
@click.option("--template", "html_template", default=None,
              help="HTML template name (in templates/) or path to a .html file. Only used with -f html. Defaults to 'default'.")
def cli(urls, fmt, output_stem, user_name, bot_name_override, time_fmt,
        no_thoughts, no_sources, no_header, html_template):
    """Scrape one or more Poe.com shared conversation URLs and export them.
    Inputs can also be poe-scrape JSON files for reconversion (e.g. to HTML).

    \b
    Examples:
      python poe_scrape.py https://poe.com/s/abc123
      python poe_scrape.py https://poe.com/s/abc https://poe.com/s/def -f json
      python poe_scrape.py https://poe.com/s/abc -f html -o my_chat --no-thoughts
      python poe_scrape.py export.json -f html --template light
    """
    opts = {
        "time_fmt": time_fmt,
        "no_thoughts": no_thoughts,
        "no_sources": no_sources,
        "no_header": no_header,
    }

    if html_template and fmt != "html":
        click.echo("  Warning: --template is only used with -f html and will be ignored.", err=True)

    html_template = html_template or "default"

    # Validate template path early so we fail before scraping anything
    if fmt == "html":
        _resolve_template(html_template)

    stem = output_stem or f"poe_{date.today().strftime('%Y-%m-%d')}"
    ext = _EXT[fmt]
    multi = len(urls) > 1

    for i, url in enumerate(urls):
        filename = f"{stem}_{i + 1}{ext}" if multi else f"{stem}{ext}"

        is_json_file = url.endswith(".json") and Path(url).is_file()

        if is_json_file:
            click.echo(f"Converting {url} ...")
            if fmt == "json":
                click.echo(f"  Error: input is already a JSON file — choose a different output format.", err=True)
                continue
            try:
                messages, meta = load_json_export(url, bot_name_override, user_name)
            except click.ClickException as e:
                click.echo(f"  Error: {e.format_message()}", err=True)
                continue
        else:
            click.echo(f"Scraping {url} ...")
            _validate_url(url)

            try:
                raw = asyncio.run(scrape_url(url))
            except Exception as e:
                click.echo(f"  Error scraping {url}: {e}", err=True)
                continue

            messages = parse_messages(raw.get("items", []), opts)
            bot_name = bot_name_override or _merged_bot_name(messages, raw.get("botName") or "Bot")
            meta = {
                "url": url,
                "bot_name": bot_name,
                "user_name": user_name,
            }

        if not any(item.get("type") == "message" for item in messages):
            click.echo(f"  Error: no messages found — is this a valid shared conversation link?", err=True)
            continue

        try:
            if fmt == "md":
                output = format_md(messages, meta, opts)
            elif fmt == "json":
                output = format_json(messages, meta, opts)
            else:
                output = format_html(messages, meta, opts, template=html_template)
        except jinja2.TemplateError as e:
            click.echo(f"  Error: template rendering failed: {e}", err=True)
            continue

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(output)
        except OSError as e:
            click.echo(f"  Error writing {filename}: {e}", err=True)
            continue

        click.echo(f"  Saved: {filename}")


if __name__ == "__main__":
    cli()
