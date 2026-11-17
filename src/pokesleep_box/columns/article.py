"""Page bodies for a column and for the columns index.

Every column page has the same layout: the question as title, the answer
(結論) first, then the column's own sections (figures, tables), how to read
them, assumptions and limits, and where the numbers came from.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .page import bullets
from .svg import esc


def _provenance(doc: Dict[str, Any]) -> str:
    prov = doc.get("provenance", {})
    rows = [("計算した日", prov.get("generated_on") or "不明"),
            ("ゲームのバージョン", prov.get("game_version") or "不明"),
            ("計算エンジン", prov.get("engine") or "使っていません（育成コスト表だけで計算）"),
            ("計算コード", prov.get("generator") or "不明")]
    items = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows)
    sources = prov.get("sources") or []
    source_html = f"<h3>参考にしたデータ</h3>{bullets(sources)}" if sources else ""
    return (f'<section class="card prov"><h2>データの出どころ</h2><dl>{items}</dl>{source_html}'
            '<p class="meta">数値はこのリポジトリの計算コードで出したものです。'
            '<code>pokesleep-box columns-data</code> で計算し直せます。</p></section>')


def render_article(column, doc: Dict[str, Any], article) -> str:
    parts = [f'<header class="hero"><div class="eyebrow">コラム</div><h1>{esc(column.title)}</h1></header>',
             f'<section class="card answer" aria-label="結論"><h2>結論</h2>{bullets(article.conclusion)}</section>']
    for section in article.sections:
        parts.append(f'<section class="card"><h2>{esc(section.heading)}</h2>{section.html}</section>')
    if article.how_to_read:
        parts.append(f'<section class="card"><h2>図と表の見方</h2>{bullets(article.how_to_read)}</section>')
    limits = list(doc.get("assumptions", [])) + list(article.limits)
    if limits:
        parts.append(f'<section class="card"><h2>前提と限界</h2>{bullets(limits)}</section>')
    parts.append(_provenance(doc))
    return "".join(parts)


def render_index(entries: List[Tuple[Any, Any]]) -> str:
    if entries:
        items = "".join(f'<li><a href="{esc(c.slug)}.html">{esc(c.title)}</a><p>{esc(a.takeaway)}</p></li>'
                        for c, a in entries)
        listing = f'<ul class="list">{items}</ul>'
    else:
        listing = '<p class="meta">まだコラムがありません。</p>'
    return ('<header class="hero"><div class="eyebrow">Sleepbox Compass</div><h1>コラム</h1>'
            '<p class="meta">育成の迷いどころを、ゲームのデータと計算で確かめる短い記事です。'
            '自分のボックスのデータは使っていません。</p></header>'
            f'<section class="card">{listing}</section>')
