"""Inline SVG chart helpers for the columns pages.

Charts are plain SVG strings with no script, so they work offline and in any
browser. Colors come from CSS custom properties defined in ``page.CSS``
(``--series-1`` .. ``--series-8``, ``--ink``, ``--muted``, ``--line``), so
light and dark mode switch without regenerating the SVG. Every mark carries
a ``<title>`` so hovering (or a long press) shows its exact value, and every
chart has an accessible title and description.
"""

from __future__ import annotations

import html
import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

Number = float
Formatter = Callable[[Number], str]

WIDTH = 640
FONT = 13


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_int(value: Number) -> str:
    """12345.6 -> '12,346'."""
    return f"{int(round(value)):,}"


def fmt_man(value: Number) -> str:
    """Compact Japanese units: 1141655 -> '114.2万', 9440 -> '9,440'."""
    if abs(value) >= 10000:
        text = f"{value / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return fmt_int(value)


def nice_ticks(high: Number, count: int = 5, low: Number = 0) -> List[Number]:
    """Round tick values from ``low`` up to at least ``high``."""
    span = max(high - low, 1e-9)
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step
    ticks = []
    value = start
    while value < high + step * 0.999:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _frame(title: str, desc: str, height: int, body: str, uid: str) -> str:
    return (f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" role="img" '
            f'aria-labelledby="{uid}-t {uid}-d" xmlns="http://www.w3.org/2000/svg" '
            f'font-size="{FONT}">'
            f'<title id="{uid}-t">{esc(title)}</title><desc id="{uid}-d">{esc(desc)}</desc>'
            f'{body}</svg>')


def _uid(title: str) -> str:
    # Stable id so pages are reproducible; collisions only matter within a page.
    return "c" + format(abs(hash_text(title)) % 10 ** 8, "08d")


def hash_text(text: str) -> int:
    value = 0
    for char in text:
        value = (value * 131 + ord(char)) % (2 ** 31)
    return value


class _Plot:
    """Plot-area geometry shared by the chart types."""

    def __init__(self, height: int, y_max: Number, y_min: Number = 0,
                 left: int = 64, right: int = 16, top: int = 18, bottom: int = 48):
        self.height, self.left, self.right, self.top, self.bottom = height, left, right, top, bottom
        self.ticks = nice_ticks(y_max, low=y_min)
        self.y_min, self.y_max = self.ticks[0], self.ticks[-1]
        self.inner_w = WIDTH - left - right
        self.inner_h = height - top - bottom

    def y(self, value: Number) -> float:
        frac = (value - self.y_min) / ((self.y_max - self.y_min) or 1)
        return self.top + self.inner_h * (1 - frac)

    def grid(self, y_format: Formatter, y_label: str) -> str:
        parts = []
        for tick in self.ticks:
            y = self.y(tick)
            parts.append(f'<line class="grid" x1="{self.left}" x2="{WIDTH - self.right}" '
                         f'y1="{y:.1f}" y2="{y:.1f}"/>'
                         f'<text class="tick" x="{self.left - 6}" y="{y + 4:.1f}" '
                         f'text-anchor="end">{esc(y_format(tick))}</text>')
        parts.append(f'<text class="axis-label" x="{self.left}" y="{self.top - 6}" '
                     f'text-anchor="start">{esc(y_label)}</text>')
        return "".join(parts)

    def x_label(self, label: str) -> str:
        return (f'<text class="axis-label" x="{self.left + self.inner_w / 2:.1f}" '
                f'y="{self.height - 6}" text-anchor="middle">{esc(label)}</text>')


def line_chart(series: Sequence[Dict], *, title: str, desc: str, x_label: str, y_label: str,
               y_format: Formatter = fmt_man, x_ticks: Optional[Iterable[Number]] = None,
               markers: Sequence[Tuple[Number, str]] = (), height: int = 320,
               y_min: Optional[Number] = None, x_format: Formatter = fmt_int) -> str:
    """Lines over a numeric x axis.

    ``series`` items: ``{"name": str, "points": [(x, y), ...], "slot": 1..8}``.
    ``markers`` draws labelled dashed vertical lines at x positions.
    """
    xs = [x for s in series for x, _ in s["points"]]
    ys = [y for s in series for _, y in s["points"]]
    x_lo, x_hi = min(xs), max(xs)
    low = 0 if y_min is None else y_min
    plot = _Plot(height, max(ys), low)

    def px(x: Number) -> float:
        return plot.left + plot.inner_w * (x - x_lo) / ((x_hi - x_lo) or 1)

    parts = [plot.grid(y_format, y_label)]
    for x, label in markers:
        # Labels sit right of the line, or left of it near the right edge.
        near_edge = px(x) > WIDTH - plot.right - 60
        label_x, anchor = (px(x) - 4, "end") if near_edge else (px(x) + 4, "start")
        parts.append(f'<line class="marker" x1="{px(x):.1f}" x2="{px(x):.1f}" '
                     f'y1="{plot.top}" y2="{plot.top + plot.inner_h}"/>'
                     f'<text class="marker-label" x="{label_x:.1f}" y="{plot.top + 12}" '
                     f'text-anchor="{anchor}">{esc(label)}</text>')
    for tick in (x_ticks if x_ticks is not None else nice_ticks(x_hi, low=x_lo)):
        if x_lo <= tick <= x_hi:
            parts.append(f'<text class="tick" x="{px(tick):.1f}" y="{plot.top + plot.inner_h + 18}" '
                         f'text-anchor="middle">{esc(x_format(tick))}</text>')
    parts.append(plot.x_label(x_label))
    for s in series:
        slot = int(s.get("slot", 1))
        path = " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{plot.y(y):.1f}"
                        for i, (x, y) in enumerate(s["points"]))
        parts.append(f'<path class="line s{slot}" d="{path}"><title>{esc(s["name"])}</title></path>')
        # Dots carry the hover values; dense series get smaller dots.
        radius = 3.5 if len(s["points"]) <= 25 else 2
        for x, y in s["points"]:
            parts.append(f'<circle class="dot s{slot}" cx="{px(x):.1f}" cy="{plot.y(y):.1f}" r="{radius}">'
                         f'<title>{esc(s["name"])} {esc(x_format(x))}: {esc(y_format(y))}</title>'
                         f'</circle>')
    return _frame(title, desc, height, "".join(parts), _uid(title))


def bar_chart(categories: Sequence[str], series: Sequence[Dict], *, title: str, desc: str,
              y_label: str, x_label: str = "", y_format: Formatter = fmt_man,
              value_labels: bool = True, tick_labels: Optional[Sequence[str]] = None,
              highlight: Optional[Iterable[int]] = None, height: int = 320) -> str:
    """Vertical (grouped) bars.

    ``series`` items: ``{"name": str, "values": [...], "slot": 1..8}``, one
    value per category. ``highlight`` lists category indexes drawn with the
    accent class (used to mark jumps). ``tick_labels`` overrides the axis
    text per category ("" hides it) for charts with many bars. Value labels are printed above bars
    only when there are few bars, to avoid clutter.
    """
    highlight = set(highlight or ())
    values = [v for s in series for v in s["values"]]
    plot = _Plot(height, max(values))
    group_w = plot.inner_w / len(categories)
    gap = 2
    bar_w = max(1.0, (group_w * (0.78 if len(series) > 1 else 0.82)) / len(series) - gap)
    show_values = value_labels and len(categories) * len(series) <= 16
    parts = [plot.grid(y_format, y_label)]
    for index, category in enumerate(categories):
        x0 = plot.left + group_w * index + (group_w - (bar_w + gap) * len(series) + gap) / 2
        for s_index, s in enumerate(series):
            value = s["values"][index]
            x = x0 + s_index * (bar_w + gap)
            y = plot.y(value)
            base = plot.y(plot.y_min)
            cls = f"bar s{int(s.get('slot', 1))}" + (" hi" if index in highlight else "")
            radius = min(4.0, bar_w / 2)
            parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                         f'height="{max(0.0, base - y):.1f}" rx="{radius:.1f}">'
                         f'<title>{esc(category)} {esc(s["name"])}: {esc(y_format(value))}</title></rect>')
            if show_values:
                parts.append(f'<text class="value" x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" '
                             f'text-anchor="middle">{esc(y_format(value))}</text>')
        tick = category if tick_labels is None else tick_labels[index]
        if tick:
            parts.append(f'<text class="tick" x="{plot.left + group_w * (index + .5):.1f}" '
                         f'y="{plot.top + plot.inner_h + 18}" text-anchor="middle">'
                         f'{esc(tick)}</text>')
    if x_label:
        parts.append(plot.x_label(x_label))
    return _frame(title, desc, height, "".join(parts), _uid(title))


def legend(series: Sequence[Dict]) -> str:
    """HTML legend (scales with the page text, unlike SVG text)."""
    items = "".join(f'<li><span class="swatch s{int(s.get("slot", 1))}"></span>{esc(s["name"])}</li>'
                    for s in series)
    return f'<ul class="legend">{items}</ul>'
