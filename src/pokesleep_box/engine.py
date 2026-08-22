from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .core import ANCHORS, ROLES, now


class EngineUnavailable(RuntimeError):
    pass


def run_engine(payload: Mapping[str, Any], command: str = "engine/bin/pokesleep-engine") -> Dict[str, Any]:
    executable = Path(command)
    if not executable.exists():
        raise EngineUnavailable(
            "Neroli’s Labブリッジが未ビルドです。engine/README.mdの手順でセットアップしてください"
        )
    proc = subprocess.run(
        [str(executable)], input=json.dumps(payload, ensure_ascii=False), text=True,
        capture_output=True, check=False,
    )
    if proc.returncode:
        raise EngineUnavailable(proc.stderr.strip() or "計算エンジンの実行に失敗しました")
    return json.loads(proc.stdout)


def verify(db, command: str = "engine/bin/pokesleep-engine", tolerance: int = 0,
           strict_below_level: int = 56) -> Dict[str, int]:
    rows = db.execute("SELECT * FROM individual ORDER BY box_index").fetchall()
    counts = {"strict": 0, "tolerant": 0, "failed": 0}
    if not rows:
        return counts
    payload = {"mode": "verify", "tolerance": tolerance,
               "strictBelowLevel": strict_below_level,
               "instances": [{"uid": row["uid"], "displayedSp": row["sp"],
                              "instance": individual_to_engine(dict(row))} for row in rows]}
    response = run_engine(payload, command)
    for result in response.get("results", []):
        mode = result.get("mode", "failed")
        verified = bool(result.get("match")) and mode == "strict"
        db.execute("""UPDATE individual SET sp_computed=?,sp_diff=?,verify_mode=?,verified=?
                      WHERE uid=?""", (result.get("computedSp"), result.get("diff"),
                                      mode if mode in counts else "failed", verified, result["uid"]))
        counts[mode if mode in counts else "failed"] += 1
    db.commit()
    return counts


def evaluate(db, command: str = "engine/bin/pokesleep-engine") -> int:
    rows = db.execute("SELECT * FROM individual ORDER BY box_index").fetchall()
    response = run_engine({"mode": "evaluate", "anchors": list(ANCHORS),
                           "instances": [{"uid": r["uid"], "instance": individual_to_engine(dict(r))}
                                         for r in rows]}, command)
    version = response.get("engineVersion", "nerolis-lab")
    valuation = response.get("valuationHash", "default")
    count = 0
    for result in response.get("results", []):
        for anchor, values in result.get("scores", {}).items():
            for role in ROLES:
                if role in values:
                    db.execute("INSERT OR REPLACE INTO evaluation VALUES (?,?,?,?,?,?,?,?,?)",
                               (result["uid"], int(anchor), role, float(values[role]),
                                values.get("percentile"), values.get("deltaTeam"), version,
                                valuation, now()))
                    count += 1
    db.commit()
    return count


def individual_to_engine(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {"species": row["species"], "level": row["level"], "nature": row["nature"],
            "ingredients": json.loads(row.get("ingredients_json") or "[]"),
            "subskills": json.loads(row.get("subskills_json") or "[]"),
            "mainSkill": row["main_skill"], "skillLevel": row["skill_level"],
            "ribbon": 0}
