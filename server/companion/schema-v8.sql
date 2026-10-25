CREATE TABLE mia_astrbot_turns (
    turn_key TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    memory_suppressed INTEGER NOT NULL DEFAULT 0 CHECK(memory_suppressed IN (0,1)),
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('prepared','sending','completed','failed','expired')),
    payload_json TEXT,
    completion_id TEXT,
    text_hash TEXT,
    effects_json TEXT,
    result_json TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX mia_astrbot_completion ON mia_astrbot_turns(completion_id)
    WHERE completion_id IS NOT NULL;
CREATE INDEX mia_astrbot_session ON mia_astrbot_turns(session_id,created_at);
