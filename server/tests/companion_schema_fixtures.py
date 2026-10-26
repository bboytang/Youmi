"""Remove ONLY empty V11 extension objects when constructing historical fixtures."""


def remove_v11(db):
    db.execute('DROP INDEX youmi_memory_job_turn')
    for name in ('youmi_memory_source_state', 'youmi_memory_turn_state',
                 'youmi_cognitive_state', 'youmi_memory_replacements'):
        db.execute('DROP TABLE ' + name)
