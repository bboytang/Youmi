-- Keep registrations after owner removal so orphaned private segments can be purged.
-- Paths are derived internally from the canonical DB path, never stored here.
CREATE TABLE youmi_memory_cache_partitions (
    owner TEXT PRIMARY KEY,
    writing INTEGER NOT NULL DEFAULT 0 CHECK(writing IN (0,1)),
    last_error TEXT CHECK(last_error IN ('busy','io','database'))
);
