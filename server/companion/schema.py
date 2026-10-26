"""Atomic additive migrations; legacy fact tables and positional writes stay valid."""

import hashlib
import json
from pathlib import Path
import sqlite3
import time


LEGACY_COLUMNS = {
    'owners': ['owner', 'revision', 'paused', 'version'],
    'memories': ['id', 'owner', 'category', 'content', 'confirmed', 'created_at', 'updated_at'],
    'sources': ['memory_id', 'session_id', 'fingerprint', 'excerpt', 'created_at'],
    'traits': ['owner', 'name', 'description', 'evidence_ids', 'updated_at'],
    'checkpoints': ['owner', 'checkpoint_id', 'created_at'],
}


def _statements(source):
    pending = ''
    for line in source.splitlines(True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip():
        raise ValueError('Incomplete Youmi schema statement')


def migrate(db):
    if db.in_transaction:
        raise ValueError('Youmi schema migration requires its own transaction')
    migrations = [Path(__file__).with_name(name).read_text(encoding='utf-8')
                  for name in ('schema.sql', 'schema-v2.sql', 'schema-v3.sql', 'schema-v4.sql', 'schema-v5.sql', 'schema-v6.sql', 'schema-v7.sql', 'schema-v8.sql', 'schema-v9.sql', 'schema-v10.sql', 'schema-v11.sql', 'schema-v12.sql', 'schema-v13.sql')]
    digests = [hashlib.sha256(sql.encode()).hexdigest() for sql in migrations]
    db.execute('BEGIN IMMEDIATE')
    try:
        for table, expected in LEGACY_COLUMNS.items():
            actual = [row[1] for row in db.execute(f'PRAGMA table_info({table})')]
            if actual != expected:
                raise ValueError(f'Unreviewed legacy memory schema: {table}')
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mia_schema_migrations'").fetchone()
        if exists:
            rows = db.execute('SELECT version,checksum,schema_checksum FROM mia_schema_migrations ORDER BY version').fetchall()
            if (not 1 <= len(rows) <= len(migrations)
                    or [(row[0], row[1]) for row in rows] != list(enumerate(digests[:len(rows)], 1))
                    or rows[-1][2] != _schema_checksum(db)):
                raise ValueError('Unreviewed Youmi schema version or checksum')
            applied = len(rows)
        else:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name GLOB 'mia_*' OR name GLOB 'youmi_*' LIMIT 1").fetchone():
                raise ValueError('Unversioned Youmi schema objects; refusing to overwrite')
            db.execute('CREATE TABLE mia_schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,schema_checksum TEXT NOT NULL,applied_at INTEGER NOT NULL)')
            applied = 0
        # executescript() implicitly commits; execute complete DDL statements instead.
        for version in range(applied + 1, len(migrations) + 1):
            for statement in _statements(migrations[version - 1]):
                db.execute(statement)
            db.execute('INSERT INTO mia_schema_migrations(version,checksum,schema_checksum,applied_at) VALUES (?,?,?,?)',
                       (version, digests[version - 1], _schema_checksum(db), int(time.time())))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _schema_checksum(db):
    objects = [tuple(row) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name GLOB 'mia_*' OR name GLOB 'youmi_*' ORDER BY type,name")]
    return hashlib.sha256(json.dumps(objects, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def check_worker_rollback(db):
    """Offline precondition, NOT a schema downgrade or a process-quiescence proof.

    Preserve the request ledger. A pre-V2 binary rejects this schema; rollback
    uses a V2-aware worker with V1 admission after draining/stopping V2 work.
    """
    active = db.execute("SELECT COUNT(*) FROM mia_job_policy p JOIN mia_jobs j ON j.id=p.job_id "
        "WHERE j.state IN ('pending','extracting','validated','retry_wait')").fetchone()[0]
    unresolved = db.execute('SELECT COUNT(*) FROM mia_model_requests WHERE native_finished_at IS NULL').fetchone()[0]
    if active or unresolved:
        raise ValueError('V2 rollback requires terminal tasks and quiescent, reconciled native requests')
    return {'compatible_worker_required': True, 'ledger_preserved': True}


def check_memory_rollback(db):
    """Read-only offline precondition; never a downgrade or a quiescence proof."""
    check_worker_rollback(db)
    active = db.execute("SELECT COUNT(*) FROM mia_jobs WHERE state IN "
                        "('pending','extracting','validated','retry_wait')").fetchone()[0]
    turns = db.execute("SELECT COUNT(*) FROM mia_astrbot_turns WHERE status IN ('prepared','sending')").fetchone()[0]
    if active or turns:
        raise ValueError('Memory rollback requires terminal jobs and drained turns')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Memory rollback requires valid evidence relationships')
    return {'compatible_build_required': True, 'schema_version': db.execute(
        'SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0],
        'ledger_preserved': True, 'offline_precondition_only': True}
