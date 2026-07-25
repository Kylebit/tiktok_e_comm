# -*- coding: utf-8 -*-
"""Build combined HTML for sticker + wreath suite flows with final images."""

from __future__ import annotations

import base64
import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "image_suite_flow_detail.html"
DESKTOP = Path(r"C:\Users\Windows11\Desktop\ORB_读图理解与分镜Prompt流程详解.html")
DESKTOP_ASSETS = Path(r"C:\Users\Windows11\Desktop\ORB_suite_images_all")

PRODUCTS = [
    {
        "key": "sticker_660007",
        "label": "产品 A · 玫瑰木栅栏墙贴（SKU 660007 MY）",
        "dir": ROOT / "outputs" / "image_suite_plan" / "660007_my",
        "title_fallback": "Pelekat Dinding Bunga / 660007",
    },
    {
        "key": "wreath_autumn",
        "label": "产品 B · 秋季南瓜枫叶花环",
        "dir": ROOT / "outputs" / "image_suite_plan" / "wreath_autumn",
        "title_fallback": "秋季南瓜枫叶花环门窗挂饰 感恩节庭院向日葵枫叶藤圈壁",
    },
]


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def load_product(meta: dict) -> dict:
    d = meta["dir"]
    plan = json.loads((d / "suite_plan.json").read_text(encoding="utf-8"))
    shots = json.loads((d / "shot_prompts.json").read_text(encoding="utf-8"))
    gen = json.loads((d / "generated" / "generation_result.json").read_text(encoding="utf-8"))
    src_urls = {}
    su = d / "source_urls.json"
    if su.is_file():
        src_urls = json.loads(su.read_text(encoding="utf-8"))
    return {
        **meta,
        "plan": plan,
        "shots": shots,
        "gen": gen,
        "src_urls": src_urls,
    }


def render_product(p: dict) -> str:
    plan = p["plan"]
    analysis = plan.get("analysis") or {}
    suite = plan.get("suite") or {}
    meta = plan.get("_meta") or {}
    usage = meta.get("usage") or {}
    title = meta.get("title") or p["title_fallback"]
    analyze_url = meta.get("analyze_image_url") or meta.get("image_url") or ""
    hero_url = meta.get("image_url") or p["src_urls"].get("hero_white") or analyze_url

    # copy assets
    dest = DESKTOP_ASSETS / p["key"]
    dest.mkdir(parents=True, exist_ok=True)
    for row in p["gen"]:
        lp = ROOT / str(row.get("local_path") or "")
        if lp.is_file():
            shutil.copy2(lp, dest / lp.name)
    for src in (p["dir"] / "source").glob("*.png") if (p["dir"] / "source").is_dir() else []:
        shutil.copy2(src, dest / f"source_{src.name}")

    analysis_rows = "".join(
        f"<tr><th>{esc(k)}</th><td>{esc(json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v)}</td></tr>"
        for k, v in analysis.items()
    )
    suite_rows = "".join(
        f"<tr><td><code>{esc(it.get('id'))}</code></td><td>{esc(it.get('type'))}</td>"
        f"<td>{esc(it.get('title'))}</td><td>{'✓' if it.get('selected') else '✗'}</td>"
        f"<td>{esc(it.get('focus'))}</td></tr>"
        for it in suite.get("items") or []
    )

    gallery = []
    gen_rows = []
    for row in p["gen"]:
        lp = ROOT / str(row.get("local_path") or "")
        if not lp.is_file():
            continue
        gallery.append(
            f"""
            <figure class="gal">
              <img src="{data_uri(lp)}" alt="{esc(row.get('id'))}"/>
              <figcaption>
                <code>{esc(row.get('id'))}</code> · {esc(row.get('type'))}<br/>
                <b>{esc(row.get('title'))}</b><br/>
                <span class="note">{esc(row.get('focus'))}</span><br/>
                <span class="note">task={esc(row.get('task_id'))} · {esc(row.get('elapsed_sec'))}s</span>
              </figcaption>
            </figure>
            """
        )
        gen_rows.append(
            f"<tr><td><code>{esc(row.get('id'))}</code></td><td>{esc(row.get('title'))}</td>"
            f"<td><code>{esc(row.get('task_id'))}</code></td><td>{esc(row.get('elapsed_sec'))}s</td>"
            f"<td>{'✓' if row.get('ok') else '✗'}</td></tr>"
        )

    shot_details = []
    for sh in p["shots"].get("shots") or []:
        shot_details.append(
            f"""
            <details class="shot">
              <summary><code>{esc(sh.get('id'))}</code> {esc(sh.get('title'))}</summary>
              <pre>{esc(sh.get('prompt'))}</pre>
            </details>
            """
        )

    source_block = ""
    if (p["dir"] / "source").is_dir():
        imgs = []
        for f in sorted((p["dir"] / "source").glob("*.png")):
            imgs.append(
                f'<figure class="gal"><img src="{data_uri(f)}" alt="{esc(f.name)}"/>'
                f"<figcaption>源图 {esc(f.name)}</figcaption></figure>"
            )
        source_block = f'<div class="gallery">{"".join(imgs)}</div>'
    elif hero_url:
        source_block = f'<p><img class="preview" src="{esc(hero_url)}" alt="source"/></p>'

    return f"""
    <section class="product" id="{esc(p['key'])}">
      <h2>{esc(p['label'])}</h2>
      <div class="card">
        <b>标题</b>：{esc(title)}<br/>
        <span class="badge">策划模型</span> {esc(meta.get('model') or 'gemini-3-pro-official')}
        <span class="badge">生图模型</span> nano_banana
        <span class="badge mute">成图</span> {sum(1 for r in p['gen'] if r.get('ok'))}/{len(p['gen'])}
      </div>

      <h3>最终 {sum(1 for r in p['gen'] if r.get('ok') and r.get('local_path'))} 张成图</h3>
      <div class="gallery">{''.join(gallery)}</div>

      <h3>1) 输入从哪来</h3>
      <table>
        <tr><th>标题</th><td>{esc(title)}</td></tr>
        <tr><th>读图用图</th><td><pre class="wrap">{esc(analyze_url)}</pre></td></tr>
        <tr><th>生图参考图</th><td><pre class="wrap">{esc(hero_url)}</pre></td></tr>
        <tr><th>说明</th><td>
          产品A：从 shop.db 按 SKU 取图/标题。<br/>
          产品B：用户上传的两张本地图 → ToAPI <code>/v1/uploads/images</code> 得到 HTTPS URL；
          零件拆解图用于读图策划，白底主图用于生图参考。
        </td></tr>
      </table>
      {source_block}

      <h3>2) 读处理</h3>
      <ol>
        <li>读图+套图策划：ToAPI chat/completions（视觉）</li>
        <li>本地展开分镜 prompt（模板 + STYLE-LOCK）</li>
        <li>ToAPI images/generations（nano_banana + reference_images）逐张生图并下载</li>
      </ol>

      <h3>3) 策划接口用量</h3>
      <table>
        <tr><th>prompt_tokens</th><td>{esc(usage.get('prompt_tokens'))}</td></tr>
        <tr><th>completion_tokens</th><td>{esc(usage.get('completion_tokens'))}</td></tr>
        <tr><th>total_tokens</th><td>{esc(usage.get('total_tokens'))}</td></tr>
      </table>

      <h3>4) 读理解 analysis</h3>
      <table>{analysis_rows}</table>

      <h3>5) 套图策划 suite</h3>
      <p>{esc(suite.get('summary'))}</p>
      <table>
        <tr><th>id</th><th>type</th><th>title</th><th>sel</th><th>focus</th></tr>
        {suite_rows}
      </table>

      <h3>6) 分镜 prompt（可展开）</h3>
      {''.join(shot_details)}

      <h3>7) 生图任务明细</h3>
      <table>
        <tr><th>id</th><th>title</th><th>task_id</th><th>耗时</th><th>ok</th></tr>
        {''.join(gen_rows)}
      </table>
    </section>
    """


def main() -> None:
    products = [load_product(m) for m in PRODUCTS]
    sections = "\n".join(render_product(p) for p in products)

    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>商品套图全流程详解（墙贴 + 花环，含成图）</title>
<style>
:root {{ --bg:#f6f3ee; --ink:#1c1917; --muted:#57534e; --card:#fffdf9; --line:#e7e0d5; --accent:#0f766e; --code:#1e293b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; color:var(--ink);
  background:radial-gradient(1000px 420px at 8% -8%,#dcefea 0%,transparent 55%),linear-gradient(180deg,#faf7f2,var(--bg)); line-height:1.65; }}
.wrap {{ max-width:1100px; margin:0 auto; padding:28px 18px 70px; }}
h1 {{ font-size:1.75rem; margin:0 0 10px; }}
h2 {{ font-size:1.35rem; margin:40px 0 12px; padding-bottom:8px; border-bottom:2px solid var(--accent); }}
h3 {{ font-size:1.05rem; margin:22px 0 8px; color:var(--accent); }}
.lead,.note {{ color:var(--muted); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:12px 0 16px; }}
.badge {{ display:inline-block; font-size:.75rem; font-weight:700; padding:2px 8px; border-radius:999px; background:#ccfbf1; color:#115e59; margin-right:6px; }}
.badge.mute {{ background:#e7e5e4; color:#44403c; }}
.gallery {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin:12px 0 18px; }}
figure.gal {{ margin:0; background:#fff; border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
figure.gal img {{ width:100%; display:block; background:#f8fafc; }}
figure.gal figcaption {{ padding:10px 12px; font-size:.86rem; }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; margin:8px 0 14px; }}
th,td {{ border:1px solid var(--line); padding:7px 9px; vertical-align:top; text-align:left; }}
th {{ background:#f5f0e8; width:24%; }}
pre {{ background:var(--code); color:#e2e8f0; padding:10px 12px; border-radius:10px; overflow:auto; white-space:pre-wrap; word-break:break-word; font-size:.78rem; }}
details.shot {{ margin:8px 0; border:1px solid var(--line); border-radius:10px; padding:8px 12px; background:#fff; }}
details.shot summary {{ cursor:pointer; font-weight:600; }}
img.preview {{ max-width:260px; border-radius:10px; border:1px solid var(--line); }}
.toc a {{ color:var(--accent); text-decoration:none; }}
.flow {{ display:grid; gap:8px; }}
.step {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>商品套图全流程详解（含最终成图）</h1>
  <p class="lead">同一流程跑了两款产品：墙贴 660007 + 秋季南瓜枫叶花环。页面内嵌全部成图，可离线打开。</p>

  <div class="card toc">
    <b>目录</b>
    <ol>
      <li><a href="#common">通用流程说明</a></li>
      <li><a href="#{PRODUCTS[0]['key']}">{esc(PRODUCTS[0]['label'])}</a></li>
      <li><a href="#{PRODUCTS[1]['key']}">{esc(PRODUCTS[1]['label'])}</a></li>
    </ol>
  </div>

  <h2 id="common">通用流程说明</h2>
  <div class="flow">
    <div class="step"><b>A. 读图理解 + 套图策划</b><br/>
      <code>POST /v1/chat/completions</code> · 模型 <code>gemini-3-pro-official</code><br/>
      输入：商品图 HTTPS URL + 标题 + 系统策划 Prompt<br/>
      输出：analysis（主体/材质/风格锁…）+ suite（卖点+场景+白底；花环额外含轻量文字卖点图）
    </div>
    <div class="step"><b>B. 分镜 Prompt（本地）</b><br/>
      读取 suite_plan.json 勾选项，注入 STYLE-LOCK / brand_dna / focus，展开为生图 prompt。<b>不调 ToAPI。</b>
    </div>
    <div class="step"><b>C. 真正生图</b><br/>
      <code>POST /v1/images/generations</code> · <code>nano_banana</code><br/>
      body 含 prompt + size=1:1 + reference_images=[源图]<br/>
      再 <code>GET /v1/images/generations/{{task_id}}</code> 轮询，下载 PNG。
    </div>
  </div>
  <div class="card">
    <b>关键命令</b>
    <pre>python scripts/explore_image_suite_plan.py --sku 660007 --region MY --max-tokens 6000
python scripts/explore_shot_prompts.py --plan outputs/image_suite_plan/660007_my/suite_plan.json
python scripts/explore_generate_shots.py --model nano_banana

python scripts/run_wreath_suite.py   # 花环：上传本地图→策划→分镜→生图</pre>
    密钥：<code>config/toapis.local.json</code> · 代理：<code>127.0.0.1:10808</code><br/>
    旁路图包：桌面 <code>ORB_suite_images_all/</code>
  </div>

  {sections}

  <p class="note" style="margin-top:28px">HTML 内嵌全部成图，体积较大属正常。</p>
</div>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    DESKTOP.write_text(body, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    print(f"wrote {DESKTOP} ({DESKTOP.stat().st_size // 1024} KB)")
    print(f"assets {DESKTOP_ASSETS}")


if __name__ == "__main__":
    main()
