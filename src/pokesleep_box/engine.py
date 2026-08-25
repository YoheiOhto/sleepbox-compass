from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .core import ANCHORS, ROLES, now


class EngineUnavailable(RuntimeError):
    pass


def run_engine(payload: Mapping[str, Any], command: str = "engine/bin/pokesleep-engine",
              on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    executable = Path(command)
    if not executable.exists():
        raise EngineUnavailable(
            "Neroli’s Labブリッジが未ビルドです。engine/README.mdの手順でセットアップしてください"
        )
    proc = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    proc.stdin.write(json.dumps(payload, ensure_ascii=False))
    proc.stdin.close()
    # The engine writes its full JSON result to stdout only once, at the very
    # end. Draining it concurrently avoids a deadlock: without a reader, a
    # large result can fill the stdout pipe buffer and block the child while
    # we are still waiting on stderr progress lines below.
    stdout_chunks: List[str] = []
    reader = threading.Thread(target=lambda: stdout_chunks.append(proc.stdout.read()), daemon=True)
    reader.start()
    stderr_lines: List[str] = []
    last_percent: Dict[str, int] = {}
    for raw_line in proc.stderr:
        line = raw_line.rstrip("\n")
        if line.startswith("PROGRESS ") and on_progress:
            label, done, total = line.split(" ")[1:]
            done, total = int(done), int(total)
            percent = min(100, round(done / total * 100)) if total else 0
            if last_percent.get(label) != percent:
                last_percent[label] = percent
                on_progress(f"{label}: {percent}%（{done}/{total}）")
        elif line:
            stderr_lines.append(line)
    reader.join()
    proc.wait()
    if proc.returncode:
        raise EngineUnavailable("\n".join(stderr_lines).strip() or "計算エンジンの実行に失敗しました")
    return json.loads("".join(stdout_chunks))


def verify(db, command: str = "engine/bin/pokesleep-engine", tolerance: int = 0,
           strict_below_level: int = 56,
           on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    rows = db.execute("SELECT * FROM individual ORDER BY box_index").fetchall()
    counts = {"strict": 0, "tolerant": 0, "failed": 0}
    # Individuals whose species OCR never resolved carry the "UNKNOWN"
    # placeholder (see ocr.finalize_candidate); the engine only knows real
    # species names, so they stay unverified/protected instead of being sent.
    computable = [row for row in rows if row["species"] != "UNKNOWN"]
    if not computable:
        return counts
    payload = {"mode": "verify", "tolerance": tolerance,
               "strictBelowLevel": strict_below_level,
               "instances": [{"uid": row["uid"], "displayedSp": row["sp"],
                              "instance": individual_to_engine(dict(row))} for row in computable]}
    response = run_engine(payload, command, on_progress)
    for result in response.get("results", []):
        mode = result.get("mode", "failed")
        verified = bool(result.get("match")) and mode == "strict"
        db.execute("""UPDATE individual SET sp_computed=?,sp_diff=?,verify_mode=?,verified=MAX(review_confirmed,?)
                      WHERE uid=?""", (result.get("computedSp"), result.get("diff"),
                                      mode if mode in counts else "failed", verified, result["uid"]))
        counts[mode if mode in counts else "failed"] += 1
    db.commit()
    return counts


def evaluate(db, command: str = "engine/bin/pokesleep-engine",
             team_out: Path = Path("data/private/team_plans.json"),
             on_progress: Optional[Callable[[str], None]] = None) -> int:
    from .analytics import ISLANDS

    rows = db.execute("SELECT * FROM individual ORDER BY box_index").fetchall()
    computable = [row for row in rows if row["species"] != "UNKNOWN"]
    response = run_engine({"mode": "evaluate", "anchors": list(ANCHORS),
                           "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                           "iterations": 500,
                           "instances": [{"uid": r["uid"], "instance": individual_to_engine(dict(r))}
                                         for r in computable]}, command, on_progress)
    version = response.get("engineVersion", "nerolis-lab")
    valuation = response.get("valuationHash", "default")
    count = 0
    for result in response.get("results", []):
        if result.get("energyScores"):
            db.execute("UPDATE individual SET energy_scores_json=? WHERE uid=?",
                       (json.dumps(result["energyScores"], ensure_ascii=False), result["uid"]))
        for anchor, values in result.get("scores", {}).items():
            for role in ROLES:
                if role in values:
                    db.execute("INSERT OR REPLACE INTO evaluation VALUES (?,?,?,?,?,?,?,?,?)",
                               (result["uid"], int(anchor), role, float(values[role]),
                                values.get("percentile"), values.get("deltaTeam"), version,
                                valuation, now()))
                    count += 1
    db.commit()
    team_response = run_engine({"mode": "team-evaluate", "anchors": list(ANCHORS),
                                "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                                "teamSearchIterations": 80, "teamIterations": 500,
                                "instances": [{"uid": r["uid"], "verified": bool(r["verified"]),
                                               "instance": individual_to_engine(dict(r))}
                                              for r in computable]}, command, on_progress)
    team_out.parent.mkdir(parents=True, exist_ok=True)
    team_out.write_text(json.dumps(team_response, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return count


def species_scores(command: str = "engine/bin/pokesleep-engine",
                   on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, Dict[str, Any]]:
    """Score each final-evolution species' own ideal individual on the same
    0-100 scale used for owned individuals (see core.absolute_role_scores).
    """
    from .core import absolute_role_scores

    response = run_engine({"mode": "score-references", "anchors": list(ANCHORS)}, command, on_progress)
    result: Dict[str, Dict[str, Any]] = {}
    for species, scores in response.get("species", {}).items():
        by_anchor = {int(level): roles for level, roles in scores.items()}
        role_scores = absolute_role_scores(by_anchor)
        result[species] = {"absolute_score": max(role_scores.values(), default=0.0),
                           "by_role": role_scores}
    return result


def individual_to_engine(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {"species": row["species"], "level": row["level"], "nature": row["nature"],
            "ingredients": json.loads(row.get("ingredients_json") or "[]"),
            "subskills": json.loads(row.get("subskills_json") or "[]"),
            "mainSkill": row["main_skill"], "skillLevel": row["skill_level"],
            "ribbon": 0}
