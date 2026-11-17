"""Default locations of private inputs and outputs.

Every path here is under a gitignored directory (see ``.gitignore``), so the
`sync` flow never writes personal data into the public ``site/`` or into
tracked files. Subcommands take their defaults from ``DEFAULT_PATHS``; the
optional ``"paths"`` object in ``config/settings.local.json`` overrides them
for `sync`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Mapping, Optional

SETTINGS = Path("config/settings.local.json")


@dataclass(frozen=True)
class PrivatePaths:
    inbox: Path = Path("inbox")
    db: Path = Path("data/box.sqlite")
    frames: Path = Path("frames")
    # Audit report and review page sit next to the extracted frames, which
    # they describe.
    audit: Path = Path("frames/audit_report.md")
    review: Path = Path("frames/review.html")
    vlm_cache: Path = Path("data/private/vlm_cache")
    team_plans: Path = Path("data/private/team_plans.json")
    training_plans: Path = Path("data/private/training_plans.json")
    capture_plans: Path = Path("data/private/capture_plans.json")
    benchmarks: Path = Path("data/private/species_benchmarks.json")
    seed_plans: Path = Path("data/private/seed_plans.json")
    main_seed_plans: Path = Path("data/private/main_seed_plans.json")
    site: Path = Path("site/private")
    settings: Path = SETTINGS
    engine: str = "engine/bin/pokesleep-engine"


DEFAULT_PATHS = PrivatePaths()
PUBLIC_SITE = Path("site")


def load_settings(path: Path = SETTINGS) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def private_paths(settings: Optional[Mapping[str, Any]] = None,
                  base: PrivatePaths = DEFAULT_PATHS) -> PrivatePaths:
    """Apply the ``"paths"`` overrides of the local settings to ``base``.

    The private site must never be the public demo directory ``site/``.
    """
    overrides = dict((settings or {}).get("paths") or {})
    known = {field.name: field.type for field in fields(PrivatePaths)}
    changes = {key: (str(value) if key == "engine" else Path(value))
               for key, value in overrides.items() if key in known}
    result = replace(base, **changes)
    if Path(result.site).resolve() == PUBLIC_SITE.resolve():
        raise ValueError("非公開サイトの出力先に公開用のsite/は指定できません（site/privateを使ってください）")
    return result
