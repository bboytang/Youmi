-- Durable invalidation generations, not a second fact store or a cache registry.
CREATE TABLE youmi_memory_cache_invalidations (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
    cleaned_generation INTEGER NOT NULL DEFAULT 0
        CHECK(typeof(cleaned_generation)='integer' AND cleaned_generation>=0 AND cleaned_generation<=generation)
);
INSERT INTO youmi_memory_cache_invalidations(owner,generation) SELECT owner,1 FROM owners;
CREATE INDEX youmi_memory_cache_pending ON youmi_memory_cache_invalidations(owner)
    WHERE cleaned_generation<generation;
CREATE TRIGGER youmi_memory_cache_owner_insert AFTER INSERT ON owners BEGIN
    INSERT INTO youmi_memory_cache_invalidations(owner,generation) VALUES (NEW.owner,1);
END;
CREATE TRIGGER youmi_memory_cache_content_update AFTER UPDATE OF content_epoch ON mia_scope_versions
WHEN OLD.content_epoch<>NEW.content_epoch BEGIN
    UPDATE youmi_memory_cache_invalidations SET generation=generation+1 WHERE owner=NEW.owner;
END;
CREATE TRIGGER youmi_memory_cache_owner_update AFTER UPDATE OF revision,paused ON owners
WHEN OLD.revision<>NEW.revision OR OLD.paused<>NEW.paused BEGIN
    UPDATE youmi_memory_cache_invalidations SET generation=generation+1 WHERE owner=NEW.owner;
END;
