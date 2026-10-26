-- Derived views retain IDs/versions, never a second copy of factual prose.
CREATE TABLE youmi_memory_artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('profile','note','retirement','merge')),
    item_key TEXT NOT NULL,
    title TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>0),
    job_id TEXT REFERENCES mia_jobs(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(owner,kind,item_key)
);
CREATE TABLE youmi_memory_artifact_evidence (
    artifact_id TEXT NOT NULL REFERENCES youmi_memory_artifacts(id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    content_version INTEGER NOT NULL CHECK(content_version>0),
    evidence_version INTEGER NOT NULL CHECK(evidence_version>0),
    position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 7),
    PRIMARY KEY(artifact_id,memory_id),
    UNIQUE(artifact_id,position)
);
CREATE INDEX youmi_memory_artifact_source ON youmi_memory_artifact_evidence(memory_id);
CREATE TRIGGER youmi_memory_artifact_job_scope BEFORE INSERT ON youmi_memory_artifacts
WHEN NEW.job_id IS NOT NULL BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM mia_jobs j WHERE j.id=NEW.job_id AND j.owner=NEW.owner)
        THEN RAISE(ABORT,'artifact_job_scope_mismatch') END;
END;
CREATE TRIGGER youmi_memory_artifact_identity BEFORE UPDATE OF id,owner,kind,item_key ON youmi_memory_artifacts BEGIN
    SELECT RAISE(ABORT,'artifact_identity_immutable');
END;
CREATE TRIGGER youmi_memory_artifact_scope BEFORE INSERT ON youmi_memory_artifact_evidence BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM youmi_memory_artifacts a JOIN memories m ON m.owner=a.owner
        JOIN mia_memory_meta x ON x.memory_id=m.id
        WHERE a.id=NEW.artifact_id AND m.id=NEW.memory_id AND m.confirmed=1 AND x.state='active'
            AND x.content_version=NEW.content_version AND x.evidence_version=NEW.evidence_version
            AND EXISTS (SELECT 1 FROM sources s WHERE s.memory_id=m.id)
    ) THEN RAISE(ABORT,'artifact_evidence_mismatch') END;
END;
CREATE TRIGGER youmi_memory_artifact_evidence_immutable BEFORE UPDATE ON youmi_memory_artifact_evidence BEGIN
    SELECT RAISE(ABORT,'artifact_evidence_immutable');
END;
-- Removing any supporting evidence removes the complete derived item/title.
CREATE TRIGGER youmi_memory_artifact_evidence_delete AFTER DELETE ON youmi_memory_artifact_evidence BEGIN
    DELETE FROM youmi_memory_artifacts WHERE id=OLD.artifact_id;
END;
CREATE TRIGGER youmi_memory_artifact_memory_delete BEFORE DELETE ON memories BEGIN
    DELETE FROM youmi_memory_artifacts WHERE id IN
        (SELECT artifact_id FROM youmi_memory_artifact_evidence WHERE memory_id=OLD.id);
END;
CREATE TRIGGER youmi_memory_artifact_meta_update
AFTER UPDATE OF content_version,evidence_version,state ON mia_memory_meta
WHEN OLD.content_version<>NEW.content_version OR OLD.evidence_version<>NEW.evidence_version OR OLD.state<>NEW.state BEGIN
    DELETE FROM youmi_memory_artifacts WHERE id IN
        (SELECT artifact_id FROM youmi_memory_artifact_evidence WHERE memory_id=NEW.memory_id);
END;
