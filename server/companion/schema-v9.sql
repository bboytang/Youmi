CREATE TABLE mia_affect_system (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    config_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);
CREATE TABLE mia_affect_system_turns (
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    PRIMARY KEY(owner,session_id,turn_id)
);
CREATE TABLE mia_affect_system_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX mia_affect_system_history_owner ON mia_affect_system_history(owner,id);
