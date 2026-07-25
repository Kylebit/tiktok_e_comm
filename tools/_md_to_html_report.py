# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "agent_comms" / "stage3" / "reports" / "2026-07-20_AI_ecommerce_image_report.md"
OUT = ROOT / "reports" / "cursor_image_features_report.html"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def flush_table(table_rows: list[str], body: list[str]) -> None:
    if not table_rows:
        return
    rows = [r for r in table_rows if not re.match(r"^\|?\s*-+", r)]
    html_rows: list[str] = []
    for ri, row in enumerate(rows):
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        tag = "th" if ri == 0 else "td"
        html_rows.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
    body.append('<div class="table-wrap"><table>' + "".join(html_rows) + "</table></div>")


def md_to_body(md: str) -> str:
    lines = md.splitlines()
    body: list[str] = []
    i = 0
    table_rows: list[str] = []
    in_table = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            in_table = True
            table_rows.append(line)
            i += 1
            continue
        if in_table:
            flush_table(table_rows, body)
            table_rows = []
            in_table = False

        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("### "):
            body.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("> "):
            body.append(f'<p class="meta">{inline(line[2:])}</p>')
        elif line.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            body.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
        elif line.strip() == "---":
            body.append("<hr/>")
        elif line.startswith("- "):
            items = [line[2:]]
            i += 1
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            body.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
        i += 1
    if in_table:
        flush_table(table_rows, body)

    out: list[str] = []
    sec = 0
    for b in body:
        if b.startswith("<h2>"):
            sec += 1
            b = b.replace("<h2>", f'<h2 id="sec{sec}">', 1)
        out.append(b)
    return "\n".join(out)


def main() -> None:
    md = MD.read_text(encoding="utf-8")
    article = md_to_body(md)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Orbit 商品流程图片能力 + AI 电商图片集成建议</title>
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
    line-height: 1.65;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
  .hero {{
    background: linear-gradient(135deg, #0f766e 0%, #1d4ed8 100%);
    color: #fff;
    border-radius: 18px;
    padding: 28px;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.18);
    margin-bottom: 20px;
  }}
  .hero h1 {{ margin: 0 0 10px; font-size: 28px; line-height: 1.35; }}
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
    padding: 18px 20px;
    margin: 14px 0;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
  }}
  .toc-title {{ font-weight: 700; margin-bottom: 8px; color: var(--accent); }}
  .toc ol {{ margin: 0; padding-left: 20px; }}
  .toc a {{ color: #1e293b; text-decoration: none; }}
  .toc a:hover {{ color: var(--accent); text-decoration: underline; }}
  h2 {{
    margin: 28px 0 12px;
    font-size: 22px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--accent-soft);
  }}
  h3 {{ margin: 18px 0 8px; font-size: 16px; color: #334155; }}
  p {{ margin: 8px 0; }}
  p.meta {{ color: var(--muted); font-size: 13px; }}
  ul {{ margin: 8px 0 8px 18px; }}
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
      <div class="badge">探索报告 #0035 · HTML 版</div>
      <h1>Orbit 商品流程图片能力梳理<br/>+ AI 电商图片集成建议</h1>
      <div class="sub">基于仓库 Kylebit/tiktok_e_comm 实码调研 + 公开资料 · 2026-07-20 / HTML 交付 2026-07-21<br/>自包含文件，可直接双击浏览器打开 · 不改业务代码、不 commit</div>
    </header>
    <nav class="toc card">
      <div class="toc-title">目录</div>
      <ol>
        <li><a href="#sec1">现有功能清单</a></li>
        <li><a href="#sec2">AI 电商图片趋势调研</a></li>
        <li><a href="#sec3">结合本店现状的集成建议</a></li>
      </ol>
    </nav>
    <article class="card">
{article}
    </article>
    <div class="foot">Cursor A2A task-859de0f9c6 · reports/cursor_image_features_report.html</div>
  </div>
</body>
</html>
"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
