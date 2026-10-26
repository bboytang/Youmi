-- A pin is an owner-managed retention flag, never a duplicate fact or confirmation.
CREATE TABLE youmi_memory_pins (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    pinned_at INTEGER NOT NULL
);
CREATE TRIGGER youmi_memory_pin_insert AFTER INSERT ON youmi_memory_pins BEGIN
    UPDATE owners SET revision=revision+1 WHERE owner=(SELECT owner FROM memories WHERE id=NEW.memory_id);
    UPDATE mia_scope_versions SET content_epoch=content_epoch+1 WHERE owner=(SELECT owner FROM memories WHERE id=NEW.memory_id);
    DELETE FROM youmi_memory_artifacts WHERE kind IN ('retirement','merge') AND id IN
        (SELECT artifact_id FROM youmi_memory_artifact_evidence WHERE memory_id=NEW.memory_id);
END;
CREATE TRIGGER youmi_memory_pin_delete AFTER DELETE ON youmi_memory_pins BEGIN
    UPDATE owners SET revision=revision+1 WHERE owner=(SELECT owner FROM memories WHERE id=OLD.memory_id);
    UPDATE mia_scope_versions SET content_epoch=content_epoch+1 WHERE owner=(SELECT owner FROM memories WHERE id=OLD.memory_id);
END;
CREATE TRIGGER youmi_memory_pin_immutable BEFORE UPDATE ON youmi_memory_pins BEGIN
    SELECT RAISE(ABORT,'memory_pin_immutable');
END;
