# -*- coding: utf-8 -*-
"""通用 Markdown -> 自包含 HTML 报告转换器（用于飞书直开交付物）。

用法:
  python tools/md_to_html.py <input.md> <output.html> \
      [--title "标题"] [--badge "徽标"] [--subtitle "副标题"] [--footer "页脚"]

特性: #/##/###/#### 标题、表格、有序/无序列表、代码块、引用、分隔线、
行内 **粗体** `代码` [链接](url) ![图片](url)。自动生成目录(TOC)。
输出为单文件 HTML，可直接双击在浏览器打开，也可经 8790 静态服务直开。
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def inline(s: str) -> str:
    s = esc(s)
    # 图片
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
               r'<img alt="\1" src="\2" style="max-width:100%;border-radius:10px;margin:6px 0"/>', s)
    # 链接
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    # 粗体
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    # 行内代码
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def flush_table(rows: list[str], body: list[str]) -> None:
    if not rows:
        return
    data = [r for r in rows if not re.match(r"^\|?\s*-{2,}", r)]
    if not data:
        return
    out: list[str] = []
    for ri, row in enumerate(data):
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        tag = "th" if ri == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
    body.append('<div class="table-wrap"><table>' + "".join(out) + "</table></div>")


def md_to_html(md: str, title: str, badge: str, subtitle: str, footer: str) -> str:
    lines = md.splitlines()
    body: list[str] = []
    toc: list[str] = []
    table: list[str] = []
    code: list[str] = []
    in_table = False
    in_code = False
    sec = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if in_code:
            if line.startswith("```"):
                body.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
                code = []
                in_code = False
            else:
                code.append(line)
            i += 1
            continue
        if line.startswith("```"):
            in_code = True
            i += 1
            continue
        if line.startswith("|"):
            in_table = True
            table.append(line)
            i += 1
            continue
        if in_table:
            flush_table(table, body)
            table = []
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            sec += 1
            txt = inline(line[3:])
            body.append(f'<h2 id="sec{sec}">{txt}</h2>')
            toc.append(f'<li><a href="#sec{sec}">{txt}</a></li>')
        elif line.startswith("### "):
            body.append(f'<h3>{inline(line[4:])}</h3>')
        elif line.startswith("#### "):
            body.append(f'<h4>{inline(line[5:])}</h4>')
        elif line.startswith("> "):
            q = [line[2:]]
            i += 1
            while i < len(lines) and lines[i].startswith("> "):
                q.append(lines[i][2:])
                i += 1
            body.append("<blockquote>" + "".join(f"<p>{inline(x)}</p>" for x in q) + "</blockquote>")
            continue
        elif line.strip() == "---":
            body.append("<hr/>")
        elif re.match(r"^\s*[-*] ", line):
            items = [re.sub(r"^\s*[-*] ", "", line)]
            i += 1
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                items.append(re.sub(r"^\s*[-*] ", "", lines[i]))
                i += 1
            body.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        elif re.match(r"^\s*\d+\. ", line):
            items = [re.sub(r"^\s*\d+\. ", "", line)]
            i += 1
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                items.append(re.sub(r"^\s*\d+\. ", "", lines[i]))
                i += 1
            body.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
        i += 1
    if in_table:
        flush_table(table, body)

    toc_html = ""
    if toc:
        toc_html = (
            '<nav class="toc card"><div class="toc-title">目录</div>'
            "<ol>" + "".join(toc) + "</ol></nav>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(title)}</title>
<style>
  :root {{
    --bg: #f4f7fb;
    --card: #ffffff;
    --ink: #0f172a;
    --muted: #64748b;
    --line: #e2e8f0;
    --accent: #0f766e;
    --accent-soft: #ccfbf1;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--ink);
    background:
      radial-gradient(1200px 500px at 10% -10%, #dbeafe 0%, transparent 55%),
      radial-gradient(900px 400px at 100% 0%, #ccfbf1 0%, transparent 50%),
      var(--bg);
    line-height: 1.7;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 32px 20px 64px; }}
  .hero {{
    background: linear-gradient(135deg, #0f766e 0%, #1d4ed8 100%);
    color: #fff;
    border-radius: 18px;
    padding: 28px;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.18);
    margin-bottom: 20px;
  }}
  .hero h1 {{ margin: 0 0 10px; font-size: 27px; line-height: 1.35; }}
  .hero .sub {{ opacity: 0.92; font-size: 14px; }}
  .badge {{
    display: inline-block;
    background: rgba(255,255,255,0.18);
    border: 1px solid rgba(255,255,255,0.28);
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    margin-bottom: 12px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px 22px;
    margin: 14px 0;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
  }}
  .toc-title {{ font-weight: 700; margin-bottom: 8px; color: var(--accent); }}
  .toc ol {{ margin: 0; padding-left: 20px; }}
  .toc a {{ color: #1e293b; text-decoration: none; }}
  .toc a:hover {{ color: var(--accent); text-decoration: underline; }}
  h1 {{ font-size: 26px; }}
  h2 {{
    margin: 28px 0 12px;
    font-size: 21px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--accent-soft);
    scroll-margin-top: 16px;
  }}
  h3 {{ margin: 18px 0 8px; font-size: 16px; color: #334155; }}
  h4 {{ margin: 14px 0 6px; font-size: 14px; color: #475569; }}
  p {{ margin: 8px 0; }}
  blockquote {{
    margin: 10px 0;
    padding: 10px 16px;
    border-left: 4px solid var(--accent);
    background: #f0fdfa;
    border-radius: 0 10px 10px 0;
    color: #134e4a;
  }}
  blockquote p {{ margin: 4px 0; }}
  ul, ol {{ margin: 8px 0 8px 20px; }}
  li {{ margin: 4px 0; }}
  code {{
    font-family: Consolas, "Courier New", monospace;
    background: #f1f5f9;
    padding: 1px 6px;
    border-radius: 5px;
    font-size: 0.92em;
  }}
  pre {{
    background: #0f172a;
    color: #e2e8f0;
    padding: 14px 16px;
    border-radius: 10px;
    overflow-x: auto;
    font-size: 13px;
  }}
  pre code {{ background: transparent; color: inherit; padding: 0; }}
  a {{ color: #1d4ed8; }}
  .table-wrap {{ overflow-x: auto; margin: 10px 0 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }}
  th, td {{ border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f8fafc; color: #334155; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  hr {{ border: 0; border-top: 1px solid var(--line); margin: 22px 0; }}
  .foot {{ margin-top: 28px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="badge">{esc(badge)}</div>
      <h1>{esc(title)}</h1>
      <div class="sub">{esc(subtitle)}</div>
    </header>
    {toc_html}
    <article class="card">
{body}
    </article>
    <div class="foot">{esc(footer)}</div>
  </div>
</body>
</html>
"""


def derive(md_path: Path) -> tuple[str, str, str]:
    txt = md_path.read_text(encoding="utf-8")
    first = next((l for l in txt.splitlines() if l.startswith("# ")), md_path.stem)
    title = first[2:].strip() if first.startswith("# ") else md_path.stem
    return title, "Orbit Hive · HTML 交付物", f"源文件：{md_path.name} · 经 8790 静态服务可直开"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--title")
    ap.add_argument("--badge")
    ap.add_argument("--subtitle")
    ap.add_argument("--footer")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = ROOT / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out

    md = inp.read_text(encoding="utf-8")
    d_title, d_badge, d_sub = derive(inp)
    title = args.title or d_title
    badge = args.badge or d_badge
    subtitle = args.subtitle or d_sub
    footer = args.footer or f"源文件：{inp.name} · 经 8790 静态服务可直开"

    doc = md_to_html(md, title, badge, subtitle, footer)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
