#!/usr/bin/env python3
"""Regenerate src/pokesleep_box/energy_references.json with the pinned engine.

Run from the repository root after the engine pin changes:

    PYTHONPATH=src python tools/update_energy_references.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pokesleep_box.engine import run_engine

OUT = Path("src/pokesleep_box/energy_references.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="engine/bin/pokesleep-engine")
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    response = run_engine({"mode": "energy-references", "anchors": [50, 60],
                           "iterations": args.iterations}, args.engine)
    document = {
        "_comment": [
            "Per-species ideal solo energy per day (berries + ingredients + main skill) at Lv50",
            "and Lv60: the reference of the 0-100 energy score (core.energy_score). Each level",
            "keeps the best of the three role-ideal builds (energyReferences in engine/src/cli.mjs)",
            "on an island that favors the species' own berry.",
            "Regenerate with tools/update_energy_references.py whenever the engine pin changes.",
        ],
        "engineVersion": response["engineVersion"], "iterations": response["iterations"],
        "species": {name: {"50": row["50"]["expected"], "60": row["60"]["expected"],
                           "build": {"50": row["50"]["build"], "60": row["60"]["build"]}}
                    for name, row in sorted(response["species"].items())},
        "finals": dict(sorted(response.get("finals", {}).items())),
    }
    # One line per top-level entry and per species keeps the file diff-friendly.
    def block(value):
        if not isinstance(value, dict) or not value:
            return json.dumps(value, ensure_ascii=False)
        rows = [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in value.items()]
        return "{\n" + ",\n".join(rows) + "\n }"
    body = ",\n".join(f" {json.dumps(k)}: {block(v)}" for k, v in document.items())
    OUT.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    print(f"{len(document['species'])} species -> {OUT}")


if __name__ == "__main__":
    main()
