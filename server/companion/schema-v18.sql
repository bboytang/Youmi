-- One retrieval engine for every endpoint bound to the same agent owner.
CREATE TABLE youmi_memory_engine (
    owner TEXT PRIMARY KEY REFERENCES owners(owner) ON DELETE CASCADE,
    engine TEXT NOT NULL CHECK(engine IN ('legacy','youmi')),
    version INTEGER NOT NULL CHECK(version >= 1),
    updated_at INTEGER NOT NULL
);
