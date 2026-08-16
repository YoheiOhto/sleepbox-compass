from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List


CSS = """
:root{color-scheme:light dark;--bg:#f7f8f4;--card:#fff;--ink:#21302a;--green:#34785a;--red:#b54141}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui,sans-serif}
main{max-width:920px;margin:auto;padding:20px}.hero{padding:28px 0}.tabs{display:flex;gap:8px;position:sticky;top:0;background:var(--bg);padding:10px 0}
button{border:0;border-radius:999px;padding:10px 16px;background:#dfe9e2;color:#183c2b}.active{background:var(--green);color:white}
.card{background:var(--card);border-radius:16px;padding:16px;margin:12px 0;box-shadow:0 2px 12px #0001}.meta{opacity:.7}.send{border-left:6px solid var(--red)}.keep{border-left:6px solid var(--green)}.protected{border-left:6px solid #b99420}
.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.score{padding:8px;background:#00000008;border-radius:8px}h1{font-size:clamp(25px,7vw,42px);margin:0}h2{margin-bottom:4px}
@media(prefers-color-scheme:dark){:root{--bg:#142019;--card:#1d2c24;--ink:#eef7f1}.score{background:#ffffff0b}}
"""


def render_site(items: List[Dict[str, Any]], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    safe = []
    for item in items:
        safe.append({k: item.get(k) for k in ("uid", "species", "display_name", "level", "box_index", "verdict", "reason", "evaluations")})
    data = json.dumps(safe, ensure_ascii=False).replace("</", "<\\/")
    page = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sleepbox Compass</title><style>{CSS}</style></head>
<body><main><section class="hero"><h1>Sleepbox Compass</h1><p>Lv60 / Lv80の両方から、育成と整理の候補を表示します。</p></section><nav class="tabs"><button data-tab="keep">育成キュー</button><button data-tab="send">送るリスト</button><button data-tab="protected">保護</button></nav><section id="list"></section></main>
<script>const DATA={data};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function draw(tab){{document.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));const xs=DATA.filter(x=>(x.verdict||'protected')===tab);document.querySelector('#list').innerHTML=xs.length?xs.map(x=>{{const e=x.evaluations||{{}};const best=l=>Math.max(...Object.values(e[l]||{{0:0}}));return `<article class="card ${{tab}}"><div class="meta">${{x.box_index??'-'}}番目 · Lv${{x.level??'-'}}</div><h2>${{esc(x.display_name||x.species)}}</h2><p>${{esc(x.reason)}}</p><div class="scores"><div class="score">S60<br><strong>${{best('60')}}</strong></div><div class="score">S80<br><strong>${{best('80')}}</strong></div><div class="score">伸び<br><strong>${{best('80')-best('60')}}</strong></div></div></article>`}}).join(''):'<p>該当する個体はありません。</p>'}}document.querySelectorAll('button').forEach(b=>b.onclick=()=>draw(b.dataset.tab));draw('keep');</script></body></html>'''
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
