"""Turns docs/MANUAL.md (the single source of the user manual) into the styled HTML page the app shows.
Handles the Markdown the manual uses: headings, paragraphs, bullet/numbered lists, tables, block quotes,
**bold**, *italic*, `code`. Usage: python app/manual_html.py docs/MANUAL.md out.html"""
import html
import re
import sys

CSS = """
:root { color-scheme: light dark; --ink:#1d1d1f; --muted:#6e6e73; --bg:#ffffff; --card:#f5f5f7; --line:#e3e3e8; --accent:#eb7330; }
@media (prefers-color-scheme: dark) { :root { --ink:#f5f5f7; --muted:#a1a1a6; --bg:#1e1e1e; --card:#2a2a2c; --line:#3a3a3c; } }
body { font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; color: var(--ink);
       background: var(--bg); margin: 0; padding: 28px 40px 48px; max-width: 860px; }
h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.01em; }
h1 + p { color: var(--muted); margin-top: 0; }
h2 { font-size: 19px; margin: 32px 0 8px; padding-top: 14px; border-top: 1px solid var(--line); }
h3 { font-size: 15px; margin: 20px 0 6px; }
p, li { margin: 6px 0; }
ul, ol { padding-left: 22px; }
blockquote { margin: 14px 0; padding: 10px 14px; background: var(--card); border-left: 3px solid var(--accent);
             border-radius: 6px; }
blockquote p { margin: 0; }
table { border-collapse: collapse; margin: 10px 0; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
code { font: 12px ui-monospace, Menlo, monospace; background: var(--card); padding: 1px 5px; border-radius: 4px; }
strong { font-weight: 600; }
"""


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def convert(md):
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"(#{1,3}) (.*)", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</table>")
            continue
        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(buf))}</p></blockquote>")
            continue
        if re.match(r"(- |\d+\. )", ln):
            tag = "ol" if re.match(r"\d+\. ", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"(- |\d+\. )", lines[i]):
                items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"(#|\||>|- |\d+\. )", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return out


def main(src, dst):
    md = open(src, encoding="utf-8").read()
    body = "\n".join(convert(md))
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Design My Video — User Manual</title>'
            f"<style>{CSS}</style></head><body>{body}</body></html>")
    open(dst, "w", encoding="utf-8").write(page)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
