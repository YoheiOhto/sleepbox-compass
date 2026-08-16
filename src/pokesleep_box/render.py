from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


CSS = """
:root{color-scheme:light dark;--bg:#f7f8f4;--card:#fff;--ink:#21302a;--green:#34785a;--red:#b54141}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui,sans-serif}
main{max-width:920px;margin:auto;padding:20px}.hero{padding:28px 0}.tabs{display:flex;gap:8px;position:sticky;top:0;background:var(--bg);padding:10px 0}
button{border:0;border-radius:999px;padding:10px 16px;background:#dfe9e2;color:#183c2b}.active{background:var(--green);color:white}
.card{background:var(--card);border-radius:16px;padding:16px;margin:12px 0;box-shadow:0 2px 12px #0001}.meta{opacity:.7}.send{border-left:6px solid var(--red)}.keep{border-left:6px solid var(--green)}.protected{border-left:6px solid #b99420}
.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.score{padding:8px;background:#00000008;border-radius:8px}h1{font-size:clamp(25px,7vw,42px);margin:0}h2{margin-bottom:4px}
@media(prefers-color-scheme:dark){:root{--bg:#142019;--card:#1d2c24;--ink:#eef7f1}.score{background:#ffffff0b}}
"""


def render_site(items: List[Dict[str, Any]], out: Path, teams: Optional[List[Dict[str, Any]]] = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    safe = []
    for item in items:
        safe.append({k: item.get(k) for k in ("uid", "species", "display_name", "level", "box_index", "verdict", "reason", "evaluations", "absolute_score", "absolute_by_role")})
    data = json.dumps(safe, ensure_ascii=False).replace("</", "<\\/")
    team_data = json.dumps(teams or [], ensure_ascii=False).replace("</", "<\\/")
    page = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sleepbox Compass</title><style>{CSS}</style></head>
<body><main><section class="hero"><h1>Sleepbox Compass</h1><p>4段階の絶対評価と、マップ別の最強パーティを表示します。</p></section><nav class="tabs"><button data-tab="keep">育成キュー</button><button data-tab="teams">マップ別パーティ</button><button data-tab="send">送るリスト</button><button data-tab="protected">保護</button></nav><section id="list"></section></main>
<script>const DATA={data},TEAMS={team_data};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function draw(tab){{document.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));if(tab==='teams'){{const islands=[...new Set(TEAMS.map(x=>x.island))];document.querySelector('#list').innerHTML=islands.length?islands.map(island=>`<article class="card"><h2>${{esc(island)}}</h2>${{TEAMS.filter(x=>x.island===island).map(t=>`<h3>${{t.mode==='current'?'現在最強':`Lv${{t.mode}}育成後`}} — ${{t.total_score}}</h3><ol>${{t.members.map(m=>`<li>${{esc(m.name)}} <span class="meta">${{esc(m.type||'タイプ未登録')}} · ${{m.score}}</span></li>`).join('')}}</ol>`).join('')}}</article>`).join(''):'<p>島別スコアを取り込むとパーティを提案します。</p>';return}}const xs=DATA.filter(x=>(x.verdict||'protected')===tab).sort((a,b)=>(b.absolute_score||0)-(a.absolute_score||0));document.querySelector('#list').innerHTML=xs.length?xs.map(x=>{{const e=x.evaluations||{{}};const best=l=>Math.max(...Object.values(e[l]||{{0:0}}));return `<article class="card ${{tab}}"><div class="meta">${{x.box_index??'-'}}番目 · Lv${{x.level??'-'}}</div><h2>${{esc(x.display_name||x.species)}} <small>総合 ${{x.absolute_score??0}} / 100</small></h2><p>${{esc(x.reason)}}</p><div class="scores">${{['50','60','70','80'].map(l=>`<div class="score">Lv${{l}}<br><strong>${{best(l)}}</strong></div>`).join('')}}</div><p class="meta">絶対評価: きのみ ${{x.absolute_by_role?.berry??0}} / 食材 ${{x.absolute_by_role?.ingredient??0}} / スキル ${{x.absolute_by_role?.skill??0}}</p></article>`}}).join(''):'<p>該当する個体はありません。</p>'}}document.querySelectorAll('button').forEach(b=>b.onclick=()=>draw(b.dataset.tab));draw('keep');</script></body></html>'''
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
