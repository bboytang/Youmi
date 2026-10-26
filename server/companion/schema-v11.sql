-- Completed-turn state is captured by the completion owner, never by a late job.
CREATE TABLE youmi_memory_turn_state (
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    affect_json TEXT,
    cognition_json TEXT,
    captured_at INTEGER NOT NULL,
    PRIMARY KEY(owner,session_id,turn_id)
);
CREATE INDEX youmi_memory_turn_state_retention ON youmi_memory_turn_state(owner,captured_at);

CREATE TABLE youmi_memory_source_state (
    memory_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    owner TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    content_version INTEGER NOT NULL,
    PRIMARY KEY(memory_id,session_id,fingerprint),
    FOREIGN KEY(memory_id,session_id,fingerprint)
        REFERENCES sources(memory_id,session_id,fingerprint) ON DELETE CASCADE,
    FOREIGN KEY(owner,session_id,turn_id)
        REFERENCES youmi_memory_turn_state(owner,session_id,turn_id) ON DELETE CASCADE
);
CREATE INDEX youmi_memory_source_state_turn ON youmi_memory_source_state(owner,session_id,turn_id);
CREATE INDEX youmi_memory_job_turn ON mia_jobs(owner,session_id,turn_id,state);
CREATE TRIGGER youmi_memory_source_state_scope BEFORE INSERT ON youmi_memory_source_state BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM memories m JOIN youmi_memory_turn_state t ON t.owner=m.owner
        WHERE m.id=NEW.memory_id AND m.owner=NEW.owner AND t.session_id=NEW.session_id
            AND t.turn_id=NEW.turn_id AND t.source_fingerprint=NEW.fingerprint
    ) THEN RAISE(ABORT,'memory_state_scope_mismatch') END;
END;
CREATE TRIGGER youmi_memory_source_state_immutable BEFORE UPDATE ON youmi_memory_source_state BEGIN
    SELECT RAISE(ABORT,'memory_state_link_immutable');
END;
CREATE TRIGGER youmi_memory_source_state_forget AFTER DELETE ON youmi_memory_source_state BEGIN
    DELETE FROM youmi_memory_turn_state
    WHERE owner=OLD.owner AND session_id=OLD.session_id AND turn_id=OLD.turn_id
        AND NOT EXISTS (SELECT 1 FROM youmi_memory_source_state s WHERE s.owner=OLD.owner
            AND s.session_id=OLD.session_id AND s.turn_id=OLD.turn_id)
        AND NOT EXISTS (SELECT 1 FROM mia_jobs j WHERE j.owner=OLD.owner
            AND j.session_id=OLD.session_id AND j.turn_id=OLD.turn_id
            AND j.state IN ('pending','extracting','validated','retry_wait'));
END;

CREATE TABLE youmi_cognitive_state (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    config_json TEXT NOT NULL,
    energy_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- A merge keeps both IDs and their original evidence; only the survivor recalls.
CREATE TABLE youmi_memory_replacements (
    retired_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    survivor_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    retired_version INTEGER NOT NULL,
    survivor_version INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(owner,operation_id),
    CHECK(retired_id<>survivor_id)
);
CREATE TRIGGER youmi_memory_replacements_scope BEFORE INSERT ON youmi_memory_replacements BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM memories a JOIN memories b ON a.owner=b.owner
        WHERE a.id=NEW.retired_id AND b.id=NEW.survivor_id AND a.owner=NEW.owner
    ) THEN RAISE(ABORT,'memory_replacement_scope_mismatch') END;
END;
CREATE TRIGGER youmi_memory_replacements_immutable BEFORE UPDATE ON youmi_memory_replacements BEGIN
    SELECT RAISE(ABORT,'memory_replacement_immutable');
END;
