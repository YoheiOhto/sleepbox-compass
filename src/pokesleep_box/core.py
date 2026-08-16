from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

ROLES = ("berry", "ingredient", "skill")
ANCHORS = (50, 60, 70, 80)
ANCHOR_WEIGHTS = {50: 0.15, 60: 0.30, 70: 0.20, 80: 0.35}
ABSOLUTE_REFERENCES = {"berry": 250.0, "ingredient": 250.0, "skill": 250.0}
ZERO_VALUE_SUBSKILLS = {
    "Research EXP Bonus", "Sleep EXP Bonus", "Dream Shard Bonus"
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_uid(item: Mapping[str, Any]) -> str:
    core = {
        "species": item["species"],
        "nature": item["nature"],
        "ingredients": item["ingredients"],
        "subskills": item["subskills"],
        "main_skill": item["main_skill"],
        "skill_level": item["skill_level"],
        "display_name": item.get("display_name") or "",
    }
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.executescript(Path(__file__).with_name("schema.sql").read_text())
    columns = {row["name"] for row in db.execute("PRAGMA table_info(individual)")}
    if "pokemon_type" not in columns:
        db.execute("ALTER TABLE individual ADD COLUMN pokemon_type TEXT")
    if "island_scores_json" not in columns:
        db.execute("ALTER TABLE individual ADD COLUMN island_scores_json TEXT NOT NULL DEFAULT '{}'")
    db.commit()
    return db


def import_individuals(db: sqlite3.Connection, items: Iterable[Mapping[str, Any]]) -> int:
    timestamp = now()
    count = 0
    for item in items:
        uid = item.get("uid") or canonical_uid(item)
        db.execute(
            """INSERT INTO individual
            (uid,species,display_name,level,nature,pokemon_type,island_scores_json,ingredients_json,subskills_json,
             main_skill,skill_level,sp,box_index,first_seen,last_seen,confidence,verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET level=excluded.level,sp=excluded.sp,
              box_index=excluded.box_index,last_seen=excluded.last_seen,
              confidence=excluded.confidence,verified=excluded.verified""",
            (uid, item["species"], item.get("display_name"), item.get("level"),
             item["nature"], item.get("pokemon_type"),
             json.dumps(item.get("island_scores", {}), ensure_ascii=False),
             json.dumps(item["ingredients"], ensure_ascii=False),
             json.dumps(item["subskills"], ensure_ascii=False), item["main_skill"],
             item["skill_level"], item.get("sp"), item.get("box_index"),
             timestamp, timestamp, item.get("confidence", 0.0), bool(item.get("verified"))),
        )
        for anchor_text, scores in item.get("scores", {}).items():
            for role, score in scores.items():
                if int(anchor_text) in ANCHORS and role in ROLES:
                    db.execute(
                        """INSERT OR REPLACE INTO evaluation VALUES
                        (?,?,?,?,?,?,?,?,?)""",
                        (uid, int(anchor_text), role, float(score), None, None,
                         "imported", "imported", timestamp),
                    )
        count += 1
    db.commit()
    return count


def decide(db: sqlite3.Connection, keep_top_n: int = 2,
           protected_uids: Sequence[str] = ()) -> Dict[str, int]:
    people = db.execute("SELECT * FROM individual ORDER BY species, box_index").fetchall()
    scores = {(r["uid"], r["anchor_level"], r["role"]): r["score"] for r in
              db.execute("SELECT uid,anchor_level,role,score FROM evaluation")}
    by_species: Dict[str, List[sqlite3.Row]] = {}
    for person in people:
        by_species.setdefault(person["species"], []).append(person)
    counts = {"keep": 0, "send": 0, "protected": 0}
    timestamp = now()
    for species, group in by_species.items():
        for person in group:
            uid = person["uid"]
            if uid in protected_uids:
                verdict, reason = "protected", "保護リストで指定されています"
            elif not person["verified"]:
                verdict, reason = "protected", "検証が完了していないため判定対象外です"
            elif any((uid, a, r) not in scores for a in ANCHORS for r in ROLES):
                verdict, reason = "protected", "Lv60/Lv80の全ロール評価が未完了です"
            else:
                better = []
                for anchor in ANCHORS:
                    for role in ROLES:
                        own = scores[(uid, anchor, role)]
                        n = sum(scores.get((other["uid"], anchor, role), float("-inf")) > own
                                for other in group if other["uid"] != uid)
                        better.append(n)
                dominated = all(n >= keep_top_n for n in better)
                if dominated:
                    verdict = "send"
                    reason = (f"同種の上位個体が全3ロール・Lv50/60/70/80で"
                              f"それぞれ{keep_top_n}匹以上います")
                else:
                    verdict = "keep"
                    reason = "Lv50/60/70/80のいずれかの役割で同種上位候補です"
            db.execute("INSERT OR REPLACE INTO decision VALUES (?,?,?,?)",
                       (uid, verdict, reason, timestamp))
            counts[verdict] += 1
    db.commit()
    return counts


def load_dashboard(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = db.execute("""SELECT i.*,d.verdict,d.reason FROM individual i
                         LEFT JOIN decision d ON d.uid=i.uid ORDER BY i.box_index""").fetchall()
    evaluations: Dict[str, Dict[int, Dict[str, float]]] = {}
    for row in db.execute("SELECT uid,anchor_level,role,score FROM evaluation"):
        evaluations.setdefault(row["uid"], {}).setdefault(row["anchor_level"], {})[row["role"]] = row["score"]
    result = []
    for row in rows:
        item_scores = evaluations.get(row["uid"], {})
        role_scores = absolute_role_scores(item_scores)
        result.append({**dict(row), "evaluations": item_scores,
                       "island_scores": json.loads(row["island_scores_json"] or "{}"),
                       "absolute_by_role": role_scores,
                       "absolute_score": max(role_scores.values(), default=0.0)})
    return result


def build_team_plans(items: Sequence[Mapping[str, Any]], team_size: int = 5) -> List[Dict[str, Any]]:
    """Select the exact best additive team for each island and training anchor.

    Island scores must come from the external game engine/input. Missing values
    are excluded instead of estimated in Python.
    """
    islands = sorted({island for item in items for island in item.get("island_scores", {})})
    plans: List[Dict[str, Any]] = []
    for island in islands:
        for mode in ("current", "50", "60", "70", "80"):
            candidates = []
            for item in items:
                if not item.get("verified"):
                    continue
                value = item.get("island_scores", {}).get(island, {}).get(mode)
                if value is not None:
                    candidates.append((float(value), item))
            selected = sorted(candidates, key=lambda pair: (-pair[0], pair[1]["uid"]))[:team_size]
            if selected:
                plans.append({
                    "island": island,
                    "mode": mode,
                    "total_score": round(sum(score for score, _ in selected), 2),
                    "members": [{"uid": item["uid"], "name": item.get("display_name") or item["species"],
                                 "species": item["species"], "type": item.get("pokemon_type"),
                                 "score": score} for score, item in selected],
                })
    return plans


def absolute_role_scores(evaluations: Mapping[int, Mapping[str, float]],
                         references: Mapping[str, float] = ABSOLUTE_REFERENCES,
                         weights: Mapping[int, float] = ANCHOR_WEIGHTS) -> Dict[str, float]:
    """Return stable 0-100 scores based on fixed references, never box rank."""
    result: Dict[str, float] = {}
    for role in ROLES:
        if any(role not in evaluations.get(anchor, {}) for anchor in ANCHORS):
            result[role] = 0.0
            continue
        ratio = sum(weights[a] * max(0.0, evaluations[a][role]) / references[role]
                    for a in ANCHORS)
        result[role] = round(min(100.0, ratio * 100.0), 1)
    return result
