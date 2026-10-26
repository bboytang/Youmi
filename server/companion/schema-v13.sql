CREATE TABLE youmi_memory_reflection_settings (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 60 AND 604800),
    version INTEGER NOT NULL CHECK(version>0),
    last_maintenance_at INTEGER,
    last_signature TEXT
);
CREATE TABLE youmi_memory_reflection_jobs (
    job_id TEXT PRIMARY KEY REFERENCES mia_jobs(id) ON DELETE CASCADE,
    owner TEXT NOT NULL REFERENCES owners(owner) ON DELETE CASCADE,
    settings_version INTEGER NOT NULL,
    maintenance_due INTEGER NOT NULL CHECK(maintenance_due IN (0,1)),
    maintenance_active INTEGER NOT NULL CHECK(maintenance_active IN (0,1))
);
CREATE UNIQUE INDEX youmi_memory_reflection_claim ON youmi_memory_reflection_jobs(owner) WHERE maintenance_active=1;
CREATE TRIGGER youmi_memory_reflection_job_scope BEFORE INSERT ON youmi_memory_reflection_jobs BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM mia_jobs j JOIN mia_job_policy p ON p.job_id=j.id
        WHERE j.id=NEW.job_id AND j.owner=NEW.owner AND p.version=2)
        THEN RAISE(ABORT,'reflection_job_scope_mismatch') END;
END;
CREATE TRIGGER youmi_memory_reflection_terminal AFTER UPDATE OF state ON mia_jobs
WHEN NEW.state NOT IN ('pending','extracting','validated','retry_wait') BEGIN
    UPDATE youmi_memory_reflection_jobs SET maintenance_active=0 WHERE job_id=NEW.id;
END;
