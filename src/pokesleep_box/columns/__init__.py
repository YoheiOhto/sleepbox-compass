"""Columns: short data articles that explain training decisions with numbers.

Adding a column is one new module in this package plus its data file:

1. Create ``src/pokesleep_box/columns/<name>.py`` that defines a module-level
   ``COLUMN = Column(...)`` (see ``Column``). Any module here with a
   ``COLUMN`` attribute is discovered automatically; nothing else needs
   registering, so columns written in parallel do not touch shared files.
2. ``generate(ctx)`` computes the numbers (cost tables via ``costs``, the
   engine via ``ctx.engine(payload)``) and returns
   ``{"data": ..., "assumptions": [...], "sources": [...]}``. It runs only
   locally (``pokesleep-box columns-data``), because CI has no engine.
3. ``build(doc)`` turns the stored document into an ``Article`` (answer
   first, then sections with figures/tables). It must use only ``doc`` so
   pages can be rebuilt in CI from the committed JSON.
4. Run ``pokesleep-box columns-data --only <slug>`` and commit
   ``data/columns/<slug>.json``; ``pokesleep-box columns`` (and ``demo``)
   render it into ``<site>/columns/``.

Columns must use public game data only, never the user's private box.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = 1
# data/columns/ in the repository (src layout); falls back to the working
# directory when the package is installed elsewhere.
_REPO_DATA = Path(__file__).resolve().parents[3] / "data" / "columns"
DEFAULT_DATA_DIR = _REPO_DATA if _REPO_DATA.is_dir() else Path("data/columns")


@dataclass
class Section:
    heading: str
    html: str


@dataclass
class Article:
    """What a column page shows, in reading order."""

    takeaway: str                 # one line for the index page
    conclusion: List[str]         # the answer, shown first
    sections: List[Section]       # figures and tables
    how_to_read: List[str] = field(default_factory=list)
    limits: List[str] = field(default_factory=list)  # added to the stored assumptions


@dataclass
class Column:
    slug: str                     # file names: data/columns/<slug>.json, <slug>.html
    order: int                    # position on the index page (ties sort by slug)
    title: str                    # the question the column answers
    generate: Callable[["Context"], Dict[str, Any]]
    build: Callable[[Dict[str, Any]], Article]
    uses_engine: bool = False


class Context:
    """What a generator may use. Records the engine version it ran."""

    def __init__(self, engine_command: str = "engine/bin/pokesleep-engine",
                 today: Optional[str] = None):
        self.engine_command = engine_command
        self.today = today or date.today().isoformat()
        self.engine_version: Optional[str] = None

    def engine(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from ..engine import run_engine
        response = run_engine(payload, self.engine_command)
        if response.get("engineVersion"):
            self.engine_version = response["engineVersion"]
        return response


def discover() -> List[Column]:
    """Every ``COLUMN`` defined by a module of this package, in page order."""
    found: Dict[str, Column] = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        column = getattr(module, "COLUMN", None)
        if isinstance(column, Column):
            if column.slug in found:
                raise ValueError(f"duplicate column slug: {column.slug}")
            found[column.slug] = column
    return sorted(found.values(), key=lambda c: (c.order, c.slug))


def generate_columns(out_dir: Path, engine_command: str = "engine/bin/pokesleep-engine",
                     only: Optional[List[str]] = None, today: Optional[str] = None) -> List[Path]:
    """Compute and write ``<out_dir>/<slug>.json`` for each (selected) column."""
    from ..costs import tables
    columns = discover()
    if only:
        unknown = set(only) - {c.slug for c in columns}
        if unknown:
            raise ValueError(f"unknown column(s): {', '.join(sorted(unknown))}")
        columns = [c for c in columns if c.slug in only]
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for column in columns:
        ctx = Context(engine_command, today)
        result = column.generate(ctx)
        doc = {
            "schema": SCHEMA,
            "slug": column.slug,
            "title": column.title,
            "provenance": {
                "generated_on": ctx.today,
                "generator": f"{column.generate.__module__}",
                "game_version": tables().get("game_version"),
                "engine": ctx.engine_version,
                "sources": result.get("sources", []),
            },
            "assumptions": result.get("assumptions", []),
            "data": result["data"],
        }
        path = out_dir / f"{column.slug}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written.append(path)
    return written


def load_documents(data_dir: Path = DEFAULT_DATA_DIR) -> List[tuple]:
    """(column, document) pairs for every column that has a data file."""
    pairs = []
    for column in discover():
        path = Path(data_dir) / f"{column.slug}.json"
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("schema") != SCHEMA or doc.get("slug") != column.slug:
                raise ValueError(f"{path}: unexpected schema or slug")
            pairs.append((column, doc))
    return pairs


def render_columns(out_dir: Path, data_dir: Path = DEFAULT_DATA_DIR) -> List[Path]:
    """Write ``index.html`` and one page per column into ``out_dir``."""
    from .page import document
    from .article import render_article, render_index
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    written = []
    for column, doc in load_documents(data_dir):
        article = column.build(doc)
        path = out_dir / f"{column.slug}.html"
        path.write_text(document(column.title, render_article(column, doc, article),
                                 [("← Sleepbox Compass", "../index.html"),
                                  ("コラム一覧", "index.html")]), encoding="utf-8")
        written.append(path)
        entries.append((column, article))
    index = out_dir / "index.html"
    index.write_text(document("コラム | Sleepbox Compass", render_index(entries),
                              [("← Sleepbox Compass", "../index.html")]), encoding="utf-8")
    return [index, *written]
