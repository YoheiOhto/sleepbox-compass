PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS capture (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
  captured_at TEXT NOT NULL, kind TEXT NOT NULL
    CHECK(kind IN ('detail_upper','detail_lower','box_list'))
);

CREATE TABLE IF NOT EXISTS individual (
  uid TEXT PRIMARY KEY, species TEXT NOT NULL, display_name TEXT,
  level INTEGER, nature TEXT NOT NULL, pokemon_type TEXT,
  berry TEXT,
  production_scores_json TEXT NOT NULL DEFAULT '{}',
  island_scores_json TEXT NOT NULL DEFAULT '{}',
  ingredients_json TEXT NOT NULL, subskills_json TEXT NOT NULL,
  main_skill TEXT NOT NULL, skill_level INTEGER NOT NULL, sp INTEGER,
  box_index INTEGER, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  confidence REAL NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
  sp_computed INTEGER, sp_diff INTEGER,
  verify_mode TEXT CHECK(verify_mode IN ('strict','tolerant','skipped','failed')),
  repaired INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evaluation (
  uid TEXT NOT NULL REFERENCES individual(uid), anchor_level INTEGER NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('berry','ingredient','skill')),
  score REAL NOT NULL, percentile REAL, delta_team REAL,
  engine_version TEXT NOT NULL, valuation_hash TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  PRIMARY KEY(uid, anchor_level, role, engine_version, valuation_hash)
);

CREATE TABLE IF NOT EXISTS decision (
  uid TEXT PRIMARY KEY REFERENCES individual(uid),
  verdict TEXT NOT NULL CHECK(verdict IN ('keep','send','protected')),
  reason TEXT NOT NULL, decided_at TEXT NOT NULL
);
