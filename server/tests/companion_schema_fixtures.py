"""Remove ONLY empty extension objects when constructing historical fixtures."""


def remove_v12(db):
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
