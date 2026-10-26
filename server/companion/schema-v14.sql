CREATE TABLE youmi_memory_emotion_settings (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    memory_to_emotion INTEGER NOT NULL CHECK(memory_to_emotion IN (0,1)),
    emotion_to_memory INTEGER NOT NULL CHECK(emotion_to_memory IN (0,1)),
    stimulus_weight REAL NOT NULL CHECK(stimulus_weight BETWEEN 0 AND 1),
    ranking_limit REAL NOT NULL CHECK(ranking_limit BETWEEN 0 AND 0.1),
    cooldown_seconds INTEGER NOT NULL CHECK(cooldown_seconds BETWEEN 0 AND 2592000),
    version INTEGER NOT NULL CHECK(version>0)
);
CREATE TABLE youmi_memory_emotion_cooldown (
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    content_version INTEGER NOT NULL CHECK(content_version>0),
    stimulated_at INTEGER NOT NULL,
    PRIMARY KEY(owner,memory_id,content_version)
);
CREATE INDEX youmi_memory_emotion_retention ON youmi_memory_emotion_cooldown(owner,stimulated_at);
CREATE TRIGGER youmi_memory_emotion_scope BEFORE INSERT ON youmi_memory_emotion_cooldown BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=NEW.memory_id AND m.owner=NEW.owner)
        THEN RAISE(ABORT,'memory_emotion_scope_mismatch') END;
END;
