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
  energy_scores_json TEXT NOT NULL DEFAULT '{}',
  island_scores_json TEXT NOT NULL DEFAULT '{}',
  ingredients_json TEXT NOT NULL, subskills_json TEXT NOT NULL,
  main_skill TEXT NOT NULL, skill_level INTEGER NOT NULL, sp INTEGER,
  box_index INTEGER, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  confidence REAL NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
  review_confirmed INTEGER NOT NULL DEFAULT 0,
  sp_computed INTEGER, sp_diff INTEGER,
  verify_mode TEXT CHECK(verify_mode IN ('strict','tolerant','skipped','failed')),
  repaired INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  final_evolution TEXT,
  ribbon INTEGER NOT NULL DEFAULT 0,
  never_send INTEGER NOT NULL DEFAULT 0,
  user_tags_json TEXT NOT NULL DEFAULT '[]'
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

-- All user-created planning data stays in the same local SQLite database.
CREATE TABLE IF NOT EXISTS saved_team (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, uids_json TEXT NOT NULL,
  scenario_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS production_observation (
  id INTEGER PRIMARY KEY, observed_on TEXT NOT NULL, island TEXT NOT NULL,
  energy INTEGER NOT NULL, predicted_energy INTEGER, notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS species_friendship (
  species TEXT PRIMARY KEY, friendship_level INTEGER NOT NULL,
  badge TEXT NOT NULL CHECK(badge IN ('none','bronze','silver','gold')),
  gold_slot_1 INTEGER NOT NULL DEFAULT 1,
  gold_slot_2 INTEGER NOT NULL DEFAULT 1,
  gold_slot_3 INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual', updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredient_inventory (
  ingredient TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0), updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cooking_plan (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, team_id INTEGER REFERENCES saved_team(id),
  recipe_name TEXT NOT NULL, meals_per_day INTEGER NOT NULL CHECK(meals_per_day BETWEEN 1 AND 3),
  requirements_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
