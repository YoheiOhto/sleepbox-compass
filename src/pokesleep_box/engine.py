from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .core import ANCHORS, LEVEL_SCORE_ANCHORS, ROLES, absolute_role_scores, now

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
    rows = db.execute("SELECT * FROM individual WHERE archived=0 ORDER BY box_index").fetchall()
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

    rows = db.execute("SELECT * FROM individual WHERE archived=0 ORDER BY box_index").fetchall()
    computable = [row for row in rows if row["species"] != "UNKNOWN"]
    response = run_engine({"mode": "evaluate", "anchors": list(LEVEL_SCORE_ANCHORS),
                           "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                           "iterations": 500,
                           "instances": [{"uid": r["uid"], "instance": individual_to_engine(dict(r))}
                                         for r in computable]}, command, on_progress)
    version = response.get("engineVersion", "nerolis-lab")
    valuation = response.get("valuationHash", "default")
    count = 0
    # Scores from another engine or valuation would otherwise sit next to the
    # new ones under a different primary key, and readers (which key only on
    # uid/anchor/role) would pick one of them arbitrarily.
    db.execute("DELETE FROM evaluation WHERE engine_version!=? OR valuation_hash!=?",
               (version, valuation))
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
    team_payload = {"mode": "team-evaluate", "anchors": list(LEVEL_SCORE_ANCHORS),
                    "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                    "teamSearchIterations": 80, "teamIterations": 500,
                    "instances": [{"uid": r["uid"], "verified": bool(r["verified"]),
                                   "instance": individual_to_engine(dict(r))}
                                  for r in computable]}
    inputs = team_inputs(team_payload)
    if reusable_team_plans(team_out, version, inputs):
        if on_progress:
            on_progress("team-evaluate: 前回のチーム編成を使います（編成に関わる個体の変更なし）")
        return count
    team_response = run_engine(team_payload, command, on_progress)
    team_response["inputs"] = inputs
    team_out.parent.mkdir(parents=True, exist_ok=True)
    team_out.write_text(json.dumps(team_response, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return count


def team_inputs(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Fingerprint a team-evaluate request: its settings and each member's data."""
    def digest(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    return {"settings": digest({k: v for k, v in payload.items() if k != "instances"}),
            "individuals": {entry["uid"]: digest(entry) for entry in payload["instances"]}}


def reusable_team_plans(team_out: Path, version: str, inputs: Mapping[str, Any]) -> bool:
    """True when stored team plans are still exact for ``inputs``.

    Team search is the slowest step (about 15 minutes for 250 individuals), and
    removing individuals that no plan uses -- e.g. sending low scorers to the
    Professor -- cannot change the best teams. So the stored plans are reused
    when they come from the same engine and settings, every current individual
    was evaluated last time with identical data, and every plan member is still
    present. Any new or changed individual, or a removed plan member, forces a
    fresh search.
    """
    try:
        previous = json.loads(team_out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    before = previous.get("inputs") or {}
    if previous.get("engineVersion") != version or before.get("settings") != inputs["settings"]:
        return False
    old, new = before.get("individuals") or {}, inputs["individuals"]
    if any(old.get(uid) != fingerprint for uid, fingerprint in new.items()):
        return False
    members = {member.get("uid") for plan in previous.get("plans", [])
               for member in plan.get("members", [])}
    return members <= set(new)


# Target levels for the training-gain step (part of the 育成 tab).
TRAINING_TARGETS = (50, 60)
# Candidates per island and target level that get a team simulation: the
# best by solo energy at that level (`energy_scores`), plus every member of
# the island's current team. A 240-individual box (about 220 candidates)
# takes about one minute on Apple Silicon without the cap; the cap bounds
# larger boxes.
TRAINING_POOL = 120
# Team-energy simulation length for the final comparison and for screening
# which member to replace (see `trainingGain` in engine/src/cli.mjs).
TRAINING_ITERATIONS = 300
TRAINING_SCREEN_ITERATIONS = 60

# Role-gap rules. Labels explain a gain; the simulated gain stays the ranking.
ENERGY_SKILLS = {"Energy For Everyone S", "Energizing Cheer S"}
HELP_SKILLS = {"Extra Helpful S", "Helper Boost"}
# A team "lacks" favorite berries when fewer members than this carry one.
FAVORITE_BERRY_MIN = 2
ROLE_LABELS = ("元気回復", "おてつだい支援", "好物きのみ", "食材", "スキル枠")


def member_roles(profile: Mapping[str, Any], favorites: Iterable[str]) -> List[str]:
    """Roles one team member fills, from its engine profile.

    - 元気回復: a team energy-recovery main skill (Energy For Everyone S,
      Energizing Cheer S).
    - おてつだい支援: a team help-support main skill (Extra Helpful S,
      Helper Boost) or an unlocked Helping Bonus subskill.
    - 好物きのみ: its berry is one of the island's favorite berries.
    - 食材: an ingredient (or all-round) specialist.
    - スキル枠: a skill (or all-round) specialist.
    """
    roles = []
    if profile.get("main_skill") in ENERGY_SKILLS:
        roles.append("元気回復")
    if profile.get("main_skill") in HELP_SKILLS or "Helping Bonus" in (profile.get("subskills") or []):
        roles.append("おてつだい支援")
    if profile.get("berry") in set(favorites):
        roles.append("好物きのみ")
    if profile.get("specialty") in ("ingredient", "all"):
        roles.append("食材")
    if profile.get("specialty") in ("skill", "all"):
        roles.append("スキル枠")
    return roles


def team_gaps(profiles: Iterable[Mapping[str, Any]], favorites: Iterable[str]) -> List[str]:
    """Roles no member fills (好物きのみ: fewer than FAVORITE_BERRY_MIN members)."""
    favorites = list(favorites)
    filled = [role for profile in profiles for role in member_roles(profile, favorites)]
    return [role for role in ROLE_LABELS
            if filled.count(role) < (FAVORITE_BERRY_MIN if role == "好物きのみ" else 1)]


def training_payload(db, team_plans: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the `training-gain` request from the box and the stored team plans.

    Uses each island's best current team (mode ``current``). Candidates are
    verified, non-archived individuals below the target level; per island and
    target only the TRAINING_POOL best by solo energy at that level, plus the
    team's own members, are simulated. Returns None when no island has a team.
    """
    from .analytics import ISLANDS

    rows = {row["uid"]: dict(row) for row in db.execute(
        "SELECT * FROM individual WHERE archived=0 AND species!='UNKNOWN' ORDER BY box_index")}
    teams = {}
    for plan in team_plans.get("plans", []):
        members = [m.get("uid") for m in plan.get("members", [])]
        if (str(plan.get("mode")) == "current" and plan.get("island") in ISLANDS
                and members and all(uid in rows for uid in members)):
            teams[plan["island"]] = members
    if not teams:
        return None
    candidates: Dict[str, Dict[str, List[str]]] = {}
    for island, team in teams.items():
        candidates[island] = {}
        for target in TRAINING_TARGETS:
            def solo(row: Mapping[str, Any]) -> float:
                scores = json.loads(row.get("energy_scores_json") or "{}")
                return float(((scores.get(island) or {}).get(str(target)) or {}).get("expected") or 0)
            eligible = [row for row in rows.values() if row["verified"]
                        and int(row["level"] or 0) < target]
            ranked = sorted(eligible, key=lambda row: (-solo(row), row["box_index"] or 0, row["uid"]))
            pool = [row["uid"] for row in ranked[:TRAINING_POOL]]
            pool += [row["uid"] for row in eligible if row["uid"] in team and row["uid"] not in pool]
            candidates[island][str(target)] = pool
    used = set(uid for team in teams.values() for uid in team) | {
        uid for by_target in candidates.values() for uids in by_target.values() for uid in uids}
    return {"mode": "training-gain",
            "islands": {name: list(berries) for name, berries in ISLANDS.items() if name in teams},
            "teams": teams, "candidates": candidates,
            "iterations": TRAINING_ITERATIONS, "screenIterations": TRAINING_SCREEN_ITERATIONS,
            "instances": [{"uid": uid, "instance": individual_to_engine(rows[uid])}
                          for uid in rows if uid in used]}


def summarize_training(response: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]
                       ) -> Dict[str, Any]:
    """Turn a `training-gain` response into per-island gaps and ranked entries.

    Each entry is one individual with its best gain over islands and target
    levels (``best``), every positive option (``options``) and the role labels
    it would add there (``roles``: roles it fills that the team without the
    replaced member lacks; ``provides``: every role it fills). Gains are team
    energy per day as simulated (``gain_per_day``) and times seven
    (``gain_per_week``). ``cost`` is left as None for the cost module to
    fill; ordering by gain per cost will then use it.
    """
    from .analytics import ISLANDS

    islands, options = [], {}
    for island in response.get("islands", []):
        favorites = ISLANDS.get(island["island"], ())
        profiles = island.get("team_profiles") or {}
        islands.append({"island": island["island"], "baseline": island.get("baseline"),
                        "team": island.get("team", []),
                        "gaps": team_gaps(profiles.values(), favorites)})
        for result in island.get("results", []):
            if (result.get("gain") or 0) <= 0:
                continue
            others = [profile for uid, profile in profiles.items() if uid != result.get("replaced")]
            gaps = team_gaps(others, favorites)
            provides = member_roles(result.get("profile") or {}, favorites)
            options.setdefault(result["uid"], []).append({
                "island": island["island"], "target": result["target"],
                "gain_per_day": result["gain"], "gain_per_week": result["gain"] * 7,
                "team_total": result.get("total"), "replaced": result.get("replaced"),
                "roles": [role for role in provides if role in gaps], "provides": provides})
    entries = []
    for uid, choices in options.items():
        choices.sort(key=lambda x: (-x["gain_per_day"], x["target"], x["island"]))
        row = rows.get(uid) or {}
        best = choices[0]
        entries.append({"uid": uid, "box_index": row.get("box_index"), "level": row.get("level"),
                        "best": best, "options": choices, "roles": best["roles"],
                        "provides": best["provides"],
                        # Filled later by the cost module (candies / dream shards).
                        "cost": None})
    entries.sort(key=lambda x: (-x["best"]["gain_per_day"], x["box_index"] or 0, x["uid"]))
    return {"islands": islands, "entries": entries}


def evaluate_training(db, command: str = "engine/bin/pokesleep-engine",
                      team_plans: Path = Path("data/private/team_plans.json"),
                      out: Path = Path("data/private/training_plans.json"),
                      on_progress: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
    """Compute and store training gains; reuse the stored result when inputs match.

    Returns the stored document, or None when there are no team plans yet.
    """
    try:
        plans = json.loads(team_plans.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    payload = training_payload(db, plans)
    if payload is None:
        return None
    inputs = team_inputs(payload)
    try:
        previous = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    # Exact reuse: same team-plan engine, same request settings (teams,
    # candidate lists, iterations) and identical data for every individual.
    if (previous and previous.get("engineVersion") == plans.get("engineVersion")
            and previous.get("inputs") == inputs):
        if on_progress:
            on_progress("training-gain: 前回の育成効果を使います（入力の変更なし）")
        return previous
    response = run_engine(payload, command, on_progress)
    rows = {row["uid"]: dict(row) for row in db.execute(
        "SELECT uid,box_index,level FROM individual WHERE archived=0")}
    document = {"engineVersion": response.get("engineVersion", plans.get("engineVersion")),
                "inputs": inputs, "targets": list(TRAINING_TARGETS),
                **summarize_training(response, rows)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


# Capture-gain step (捕獲 tab). The modes are the stored team plans a caught
# Pokémon is compared with: the best team now, and the best teams when the
# box's own members are trained to Lv50 / Lv60 (the capture's target level).
CAPTURE_TEAM_MODES = ("current", "50", "60")
CAPTURE_ITERATIONS = 300
CAPTURE_SCREEN_ITERATIONS = 60
# Stated assumptions about a "typical" catch (see captureRaw in cli.mjs).
CAPTURE_BUILD = {
    "nature": "Bashful",
    "subskills": [["Inventory Up S", 10], ["Helping Speed S", 25], ["Skill Trigger S", 50]],
}


def capture_payload(db, team_plans: Mapping[str, Any],
                    encounters: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the `capture-gain` request: stored teams per island and the encounter names.

    Only teams whose members are all still in the box are used. Returns None
    when no island has a current team or the encounter data is empty.
    """
    from .analytics import ISLANDS

    rows = {row["uid"]: dict(row) for row in db.execute(
        "SELECT * FROM individual WHERE archived=0 AND species!='UNKNOWN' ORDER BY box_index")}
    teams: Dict[str, Dict[str, List[str]]] = {}
    for plan in team_plans.get("plans", []):
        mode = str(plan.get("mode"))
        members = [m.get("uid") for m in plan.get("members", [])]
        if (mode in CAPTURE_TEAM_MODES and plan.get("island") in ISLANDS
                and members and all(uid in rows for uid in members)):
            teams.setdefault(plan["island"], {})[mode] = members
    teams = {island: by_mode for island, by_mode in teams.items() if "current" in by_mode}
    names = sorted({name for groups in (encounters.get("fields") or {}).values()
                    for listed in groups.values() for name in listed})
    if not teams or not names:
        return None
    used = sorted({uid for by_mode in teams.values() for team in by_mode.values() for uid in team})
    return {"mode": "capture-gain",
            "islands": {name: list(berries) for name, berries in ISLANDS.items() if name in teams},
            "teams": teams, "encounters": names,
            "typicalNature": CAPTURE_BUILD["nature"], "typicalSubskills": CAPTURE_BUILD["subskills"],
            "iterations": CAPTURE_ITERATIONS, "screenIterations": CAPTURE_SCREEN_ITERATIONS,
            "instances": [{"uid": uid, "instance": individual_to_engine(rows[uid])} for uid in used]}


def summarize_capture(response: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn a `capture-gain` response into one entry per final-evolution species.

    Each entry lists, per island, the simulated team gain per day of a
    typical catch against the current team (``gain_now``) and against the
    box's trained team at the same level (``gain``), the ideal build's gain
    against the trained team (``gain_ideal``), the member it replaces, and
    the role labels (see ``member_roles``): ``roles`` are roles it fills that
    the trained team without the replaced member lacks.
    """
    from .analytics import ISLANDS

    species: Dict[str, Dict[str, Any]] = {}
    for line in response.get("lines", []):
        for final in line.get("finals", []):
            entry = species.setdefault(final["species"], {
                "species": final["species"], "base": line["base"], "specialty": final.get("specialty"),
                "target": final.get("target"), "line": line.get("members") or [line["base"]],
                "encountered": [], "options": {}})
            for name in line.get("encountered", []):
                if name not in entry["encountered"]:
                    entry["encountered"].append(name)
    islands = []
    for island in response.get("islands", []):
        name = island["island"]
        favorites = ISLANDS.get(name, ())
        profiles = island.get("team_profiles") or {}
        islands.append({"island": name, "baselines": island.get("baselines", {}),
                        "teams": island.get("teams", {}),
                        "gaps": {mode: team_gaps(by_uid.values(), favorites)
                                 for mode, by_uid in profiles.items()}})
        for result in island.get("results", []):
            entry = species.get(result["species"])
            if entry is None:
                continue
            option = entry["options"].setdefault(name, {"island": name, "target": result["target"]})
            if result["build"] == "typical" and result["baseline"] == "current":
                option["gain_now"] = result["gain"]
            elif result["build"] == "typical":
                trained = profiles.get(result["baseline"]) or {}
                others = [p for uid, p in trained.items() if uid != result.get("replaced")]
                provides = member_roles(result.get("profile") or {}, favorites)
                option.update(gain=result["gain"], replaced=result.get("replaced"),
                              provides=provides,
                              roles=[r for r in provides if r in team_gaps(others, favorites)])
            else:
                option.update(gain_ideal=result["gain"], replaced_ideal=result.get("replaced"))
    entries = []
    for entry in species.values():
        options = sorted(entry["options"].values(),
                         key=lambda o: (-(o.get("gain") or 0), -(o.get("gain_ideal") or 0), o["island"]))
        entries.append({**entry, "options": options})
    entries.sort(key=lambda e: (-max([o.get("gain") or 0 for o in e["options"]] or [0]), e["species"]))
    return {"islands": islands, "species": entries, "names": response.get("names", {})}


def evaluate_capture(db, command: str = "engine/bin/pokesleep-engine",
                     team_plans: Path = Path("data/private/team_plans.json"),
                     encounters: Path = Path("data/seed_encounters.json"),
                     out: Path = Path("data/private/capture_plans.json"),
                     on_progress: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
    """Compute and store capture gains; reuse the stored result when inputs match.

    The request depends only on the stored teams' members and the encounter
    list, so adding or removing individuals outside every team keeps the
    stored result. Returns None without team plans or encounter data.
    """
    try:
        plans = json.loads(team_plans.read_text(encoding="utf-8"))
        encounter_data = json.loads(encounters.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    payload = capture_payload(db, plans, encounter_data)
    if payload is None:
        return None
    inputs = team_inputs(payload)
    try:
        previous = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    if (previous and previous.get("engineVersion") == plans.get("engineVersion")
            and previous.get("inputs") == inputs):
        if on_progress:
            on_progress("capture-gain: 前回の捕獲候補を使います（入力の変更なし）")
        return previous
    response = run_engine(payload, command, on_progress)
    document = {"engineVersion": response.get("engineVersion", plans.get("engineVersion")),
                "inputs": inputs, "build": CAPTURE_BUILD,
                "iterations": CAPTURE_ITERATIONS, **summarize_capture(response)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


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
    baseline = {}
    for row in db.execute("SELECT uid,anchor_level,role,score FROM evaluation"):
        baseline.setdefault(row["uid"], {}).setdefault(row["anchor_level"], {})[row["role"]] = row["score"]
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
    baseline = {}
    for row in db.execute("SELECT uid,anchor_level,role,score FROM evaluation"):
        baseline.setdefault(row["uid"], {}).setdefault(row["anchor_level"], {})[row["role"]] = row["score"]
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
            "ribbon": 0}
