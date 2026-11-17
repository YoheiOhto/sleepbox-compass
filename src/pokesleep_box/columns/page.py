"""HTML shell, tables, and styles for the columns pages.

The color tokens (``--bg``, ``--ink``, ``--brand`` ...) are taken from the
main site's ``render.CSS`` so both pages share one theme, including dark
mode. Chart series colors are added here (validated categorical palette,
stepped separately for the dark surface).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from ..render import CSS as SITE_CSS
from .svg import esc


def _site_tokens() -> str:
    lines = [line for line in SITE_CSS.splitlines()
             if line.startswith(":root{") or line.startswith("@media (prefers-color-scheme:dark){:root{")]
    if len(lines) != 2:
        raise RuntimeError("render.CSS no longer starts with the light/dark :root token lines")
    return "\n".join(lines)


SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"]


def _series_vars(colors: Sequence[str]) -> str:
    return "".join(f"--series-{i}:{c};" for i, c in enumerate(colors, 1))


CSS = _site_tokens() + "\n" + (
    ":root{" + _series_vars(SERIES_LIGHT) + "}"
    "@media (prefers-color-scheme:dark){:root{" + _series_vars(SERIES_DARK) + "}}"
    + "".join(f".s{i}{{--c:var(--series-{i})}}" for i in range(1, 9)) + """
*{box-sizing:border-box;min-width:0}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.75 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums;overflow-wrap:break-word;-webkit-font-smoothing:antialiased}
a{color:var(--brand);font-weight:600;text-underline-offset:3px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
main{max-width:820px;margin:auto;padding:0 20px 72px}
.crumbs{display:flex;flex-wrap:wrap;gap:6px 16px;padding:16px 0 0;font-size:14px}
.hero{padding:18px 2px 8px}.eyebrow{color:var(--brand);font-size:12px;font-weight:700;letter-spacing:.08em}
h1{font-size:clamp(24px,5.5vw,32px);font-weight:800;line-height:1.3;letter-spacing:-.02em;margin:6px 0}
h2{font-size:20px;font-weight:700;line-height:1.4;margin:0 0 10px}
h3{font-size:16px;margin:18px 0 8px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;margin:16px 0;box-shadow:var(--shadow-sm)}
.answer{border-left:5px solid var(--brand);background:var(--brand-soft)}
.answer ul{margin:0;padding-left:1.2em}.answer li+li{margin-top:6px}
.meta{color:var(--muted);font-size:13px}
figure{margin:12px 0 4px}
.chart-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
svg.chart{display:block;width:100%;min-width:480px;height:auto}
svg.chart text{fill:var(--muted);font-family:inherit}
svg.chart .value{fill:var(--ink);font-weight:600;font-size:12px}
svg.chart .axis-label{fill:var(--ink);font-weight:600}
svg.chart .grid{stroke:var(--line);stroke-width:1}
svg.chart .marker{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:4 4}
svg.chart .marker-label{fill:var(--ink);font-size:12px;font-weight:600}
svg.chart .line{fill:none;stroke:var(--c);stroke-width:2.5;stroke-linejoin:round}
svg.chart .dot{fill:var(--c);stroke:var(--surface);stroke-width:1.5}
svg.chart .bar{fill:var(--c)}
svg.chart .bar.hi{fill:var(--series-2)}
figcaption{color:var(--muted);font-size:13px;margin-top:6px}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;list-style:none;padding:0;margin:8px 0 0;font-size:14px}
.legend li{display:flex;align-items:center;gap:6px}
.swatch{width:14px;height:14px;border-radius:4px;background:var(--c);display:inline-block}
.table-wrap{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:13px;background:var(--surface-2)}
td.num,th.num{text-align:right}
tr.hi td{background:var(--amber-soft)}
.list{list-style:none;padding:0;margin:0}
.list li{padding:14px 0;border-bottom:1px solid var(--line)}.list li:last-child{border:0}
.list a{font-size:17px}.list p{margin:4px 0 0;color:var(--muted)}
.prov dt{font-weight:600;font-size:13px;color:var(--muted)}.prov dd{margin:0 0 8px}
@media(max-width:560px){main{padding:0 16px 56px}.card{padding:16px}}
""")


def table(headers: Sequence[str], rows: Iterable[Sequence[object]], *,
          numeric: Iterable[int] = (), highlight: Iterable[int] = (),
          caption: Optional[str] = None) -> str:
    """A small data table. ``numeric`` column indexes are right-aligned and
    ``highlight`` row indexes are shaded."""
    numeric, highlight = set(numeric), set(highlight)
    head = "".join(f'<th scope="col"{" class=num" if i in numeric else ""}>{esc(h)}</th>'
                   for i, h in enumerate(headers))
    body = []
    for r, row in enumerate(rows):
        cells = "".join(f'<td{" class=num" if i in numeric else ""}>{esc(v)}</td>'
                        for i, v in enumerate(row))
        body.append(f'<tr{" class=hi" if r in highlight else ""}>{cells}</tr>')
    cap = f"<caption class=meta>{esc(caption)}</caption>" if caption else ""
    return (f'<div class="table-wrap"><table>{cap}<thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def figure(svg: str, caption: str = "", legend_html: str = "") -> str:
    cap = f"<figcaption>{esc(caption)}</figcaption>" if caption else ""
    return f'<figure><div class="chart-wrap">{svg}</div>{legend_html}{cap}</figure>'


def bullets(items: Iterable[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>"


def document(title: str, body: str, crumbs: List[tuple]) -> str:
    links = "".join(f'<a href="{esc(href)}">{esc(label)}</a>' for label, href in crumbs)
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark">'
            '<meta name="theme-color" content="#f5f7f5" media="(prefers-color-scheme: light)">'
            '<meta name="theme-color" content="#0e1411" media="(prefers-color-scheme: dark)">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head><body><main>'
            f'<nav class="crumbs" aria-label="パンくず">{links}</nav>{body}</main></body></html>\n')
