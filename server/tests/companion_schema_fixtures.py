"""Remove ONLY empty extension objects when constructing historical fixtures."""


def remove_v17(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_pins'").fetchone():
        for name in ('insert', 'delete', 'immutable'):
            db.execute('DROP TRIGGER youmi_memory_pin_' + name)
        db.execute('DROP TABLE youmi_memory_pins')


def remove_v16(db):
    remove_v17(db)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_cache_partitions'").fetchone():
        db.execute('DROP TABLE youmi_memory_cache_partitions')


def remove_v15(db):
    remove_v16(db)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_cache_invalidations'").fetchone():
        # This is only a historical fixture, never a product downgrade.
        for name in ('owner_insert', 'owner_update', 'content_update'):
            db.execute('DROP TRIGGER youmi_memory_cache_' + name)
        db.execute('DROP TABLE youmi_memory_cache_invalidations')


def remove_v14(db):
    remove_v15(db)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_emotion_settings'").fetchone():
        db.execute('DROP TABLE youmi_memory_emotion_cooldown')
        db.execute('DROP TABLE youmi_memory_emotion_settings')


def remove_v13(db):
    remove_v14(db)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_reflection_settings'").fetchone():
        db.execute('DROP TRIGGER youmi_memory_reflection_terminal')
        db.execute('DROP TABLE youmi_memory_reflection_jobs')
        db.execute('DROP TABLE youmi_memory_reflection_settings')


def remove_v12(db):
    remove_v13(db)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='youmi_memory_artifacts'").fetchone():
        return
    assert db.execute('SELECT COUNT(*) FROM youmi_memory_artifacts').fetchone()[0] == 0
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name GLOB 'youmi_memory_artifact_*'")]
    for name in names:
        db.execute('DROP TRIGGER ' + name)
    db.execute('DROP TABLE youmi_memory_artifact_evidence')
    db.execute('DROP TABLE youmi_memory_artifacts')


def remove_v11(db):
    remove_v12(db)
    db.execute('DROP INDEX youmi_memory_job_turn')
    for name in ('youmi_memory_source_state', 'youmi_memory_turn_state',
                 'youmi_cognitive_state', 'youmi_memory_replacements'):
        db.execute('DROP TABLE ' + name)
