CREATE TABLE youmi_shared_history_settings (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE TABLE youmi_shared_history_turns (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    turn_key TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL CHECK(channel IN ('qq','passport')),
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX youmi_shared_history_owner_sequence ON youmi_shared_history_turns(owner,sequence);
CREATE INDEX youmi_shared_history_expiry ON youmi_shared_history_turns(expires_at);
CREATE TABLE youmi_shared_history_summaries (
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK(channel IN ('qq','passport')),
    source_ids TEXT NOT NULL,
    revision INTEGER NOT NULL,
    summary TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY(owner,channel)
);
