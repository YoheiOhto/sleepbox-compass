from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .core import ANCHORS, ROLES, absolute_role_scores, latest_evaluations, now

SUBSKILL_UPGRADES = {
    "Helping Speed S": "Helping Speed M", "Ingredient Finder S": "Ingredient Finder M",
    "Inventory Up S": "Inventory Up M", "Inventory Up M": "Inventory Up L",
    "Skill Level Up S": "Skill Level Up M", "Skill Trigger S": "Skill Trigger M",
}


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
        if line.startswith("PROGRESS "):
            # Progress is never part of a failure message: keeping it out of
            # `stderr_lines` stops thousands of counter lines from burying the
            # real error when no progress callback is listening.
            fields = line.split(" ")
            if on_progress and len(fields) == 4 and fields[2].isdigit() and fields[3].isdigit():
                label, done, total = fields[1], int(fields[2]), int(fields[3])
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
    rows = db.execute("SELECT * FROM individual WHERE archived=0 ORDER BY box_index").fetchall()
    counts = {"strict": 0, "tolerant": 0, "failed": 0}
    # Individuals whose species OCR never resolved carry the "UNKNOWN"
    # placeholder (see ocr.finalize_candidate); the engine only knows real
    # species names, so they stay unverified/protected instead of being sent.
    # Production simulation rejects an empty ingredient set. OCR rows that
    # still need review must remain visible in the box, but cannot participate
    # in evaluation until their positional ingredients are confirmed.
    computable = [row for row in rows
                  if row["species"] != "UNKNOWN"
                  and json.loads(row["ingredients_json"] or "[]")]
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
             simulation: Mapping[str, Any] = {},
             on_progress: Optional[Callable[[str], None]] = None) -> int:
    from .analytics import ISLANDS

    rows = db.execute("SELECT * FROM individual WHERE archived=0 ORDER BY box_index").fetchall()
    # Keep incomplete OCR rows visible in the box, but do not send rows with
    # unresolved species or an empty ingredient set to the simulation engine.
    computable = [row for row in rows
                  if row["species"] != "UNKNOWN"
                  and json.loads(row["ingredients_json"] or "[]")]
    response = run_engine({"mode": "evaluate", "anchors": list(ANCHORS),
                           "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                           "iterations": int(simulation.get("iterations", 500)),
                           "simulation": dict(simulation),
                           "instances": [{"uid": r["uid"], "instance": individual_to_engine(dict(r))}
                                         for r in computable]}, command, on_progress)
    version = response.get("engineVersion", "nerolis-lab")
    valuation = response.get("valuationHash", "default")
    count = 0
    for result in response.get("results", []):
        if result.get("finalEvolution"):
            db.execute("UPDATE individual SET final_evolution=? WHERE uid=?",
                       (result["finalEvolution"], result["uid"]))
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
                                "teamSearchIterations": int(simulation.get("teamSearchIterations", 80)),
                                "teamIterations": int(simulation.get("teamIterations", 500)),
                                "simulation": dict(simulation),
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


def evaluate_seed_upgrades(db, command: str = "engine/bin/pokesleep-engine",
                           on_progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    """Return each kept individual's best one-seed outcome available by Lv50."""
    rows = db.execute(
        """SELECT i.* FROM individual i JOIN decision d ON d.uid=i.uid
           WHERE i.verified=1 AND i.archived=0 AND d.verdict='keep' ORDER BY i.box_index""").fetchall()
    variants, metadata = [], {}
    for row in rows:
        raw = individual_to_engine(dict(row))
        owned = {name for name, _ in raw["subskills"]}
        candidates = [(name, unlock, SUBSKILL_UPGRADES[name]) for name, unlock in raw["subskills"]
                      if unlock <= 50 and name in SUBSKILL_UPGRADES
                      and SUBSKILL_UPGRADES[name] not in owned]
        for name, unlock, upgraded in candidates:
            variant = {**raw, "subskills": [[upgraded if n == name else n, level]
                                             for n, level in raw["subskills"]]}
            variant_uid = f'{row["uid"]}::seed::{name}'
            variants.append({"uid": variant_uid, "instance": variant})
            use_level = max(int(row["level"] or 0), unlock)
            random_targets = sum(candidate_unlock <= use_level
                                 for _, candidate_unlock, _ in candidates)
            metadata[variant_uid] = {"uid": row["uid"], "box_index": row["box_index"],
                                     "from": name, "to": upgraded,
                                     "candidate_count": random_targets,
                                     "use_level": use_level}
    if not variants:
        return []
    response = run_engine({"mode": "evaluate", "anchors": list(ANCHORS),
                           "instances": variants}, command, on_progress)
    baseline = latest_evaluations(db)
    best = {}
    for result in response.get("results", []):
        info = metadata[result["uid"]]
        role_scores = absolute_role_scores(
            {int(level): values for level, values in result.get("scores", {}).items()})
        score = max(role_scores.values(), default=0.0)
        base_score = max(absolute_role_scores(baseline.get(info["uid"], {})).values(), default=0.0)
        plan = {**info, "base_score": base_score, "score": score,
                "gain": round(score - base_score, 1),
                "best_role": max(role_scores, key=role_scores.get) if role_scores else None}
        if info["uid"] not in best or plan["score"] > best[info["uid"]]["score"]:
            best[info["uid"]] = plan
    useful = [plan for plan in best.values() if plan["score"] > 80.0]
    return sorted(useful, key=lambda x: (-x["score"], -x["gain"], x["box_index"]))


def evaluate_main_seed_upgrades(db, command: str = "engine/bin/pokesleep-engine",
                                on_progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    """Return kept individuals that gain value and finish above 80 with one main seed."""
    rows = db.execute(
        """SELECT i.* FROM individual i JOIN decision d ON d.uid=i.uid
           WHERE i.verified=1 AND i.archived=0 AND d.verdict='keep' ORDER BY i.box_index""").fetchall()
    variants = []
    for row in rows:
        instance = individual_to_engine(dict(row))
        variants.append({"uid": row["uid"],
                         "instance": {**instance, "skillLevel": instance["skillLevel"] + 1}})
    if not variants:
        return []
    response = run_engine({"mode": "evaluate", "anchors": list(ANCHORS),
                           "instances": variants}, command, on_progress)
    baseline = latest_evaluations(db)
    by_uid = {row["uid"]: row for row in rows}
    plans = []
    for result in response.get("results", []):
        role_scores = absolute_role_scores(
            {int(level): values for level, values in result.get("scores", {}).items()})
        score = max(role_scores.values(), default=0.0)
        base_score = max(absolute_role_scores(baseline.get(result["uid"], {})).values(), default=0.0)
        gain = round(score - base_score, 1)
        row = by_uid[result["uid"]]
        max_level = int(result.get("mainSkillMaxLevel") or 0)
        if row["skill_level"] < max_level and score > 80.0:
            plans.append({"uid": result["uid"], "box_index": row["box_index"],
                          "from_level": row["skill_level"], "to_level": row["skill_level"] + 1,
                          "max_level": max_level,
                          "base_score": base_score, "score": score, "gain": gain,
                          "best_role": max(role_scores, key=role_scores.get)})
    return sorted(plans, key=lambda x: (-x["score"], -x["gain"], x["box_index"]))


def individual_to_engine(row: Mapping[str, Any]) -> Dict[str, Any]:
    ingredient_aliases = {"Soft Potato": "Potato"}
    return {"species": row["species"], "level": row["level"], "nature": row["nature"],
            "ingredients": [[ingredient_aliases.get(name, name), amount]
                            for name, amount in json.loads(row.get("ingredients_json") or "[]")],
            "subskills": json.loads(row.get("subskills_json") or "[]"),
            "mainSkill": row["main_skill"], "skillLevel": row["skill_level"],
            "ribbon": int(row.get("ribbon") or 0)}
