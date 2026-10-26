"""Opt-in managed shadow partitions; canonical SQLite remains authoritative.

Internal only: the shadow boundary authorizes/captures a corpus before search.
There are no caller-supplied paths and no production factory enables this mode.
"""

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import sqlite3

from .embedding import EmbeddingUnavailable
from .memory_index_cache import _locked, _search_locked, _sync_directory, partition


class YoumiManagedMemoryCache:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.root = self.database.parent / ('.' + self.database.name + '.youmi-memory-cache')

    @contextmanager
    def _db(self):
        # Never create a missing canonical DB. Short writes never wait on a
        # file lock; callers release their canonical transaction before entry.
        db = sqlite3.connect(self.database.as_uri() + '?mode=rw', uri=True, timeout=.05)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def directory(self, owner):
        return partition(self.root, self.database, owner, 'mia')

    def _root(self):
        if self.root.is_symlink():
            raise OSError('Managed cache root cannot be a symlink')
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)

    @staticmethod
    def _state(db, owner):
        return db.execute('SELECT o.revision,s.content_epoch,q.generation,q.cleaned_generation '
            'FROM owners o JOIN mia_scope_versions s ON s.owner=o.owner '
            'JOIN youmi_memory_cache_invalidations q ON q.owner=o.owner WHERE o.owner=?', (owner,)).fetchone()

    def register(self, corpus):
        # The only caller passes the repository's authorized, validated corpus.
        if corpus.persona_id != 'mia':
            raise EmbeddingUnavailable('memory_changed')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._state(db, corpus.owner)
            if state is None or tuple(state[:2]) != corpus.version:
                raise EmbeddingUnavailable('memory_changed')
            db.execute('INSERT OR IGNORE INTO youmi_memory_cache_partitions(owner) VALUES (?)', (corpus.owner,))

    @staticmethod
    def _purge(path):
        # Keep the lock inode and its directory forever: removing either would
        # allow another process to acquire a different lock for this partition.
        for child in path.iterdir():
            if child.name == 'writer.lock':
                continue
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)
        _sync_directory(path)

    def _clean_locked(self, owner, path):
        with self._db() as db:
            row = self._state(db, owner)
            generation = row['generation'] if row else None
        self._purge(path)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if generation is None:
                # Registrations deliberately outlive an owner. Re-creation in
                # between is treated as a new pending generation, never erased.
                return bool(db.execute('DELETE FROM youmi_memory_cache_partitions WHERE owner=? '
                    'AND NOT EXISTS (SELECT 1 FROM owners WHERE owner=?)', (owner, owner)).rowcount)
            changed = db.execute('UPDATE youmi_memory_cache_invalidations SET cleaned_generation=? '
                'WHERE owner=? AND generation=?', (generation, owner, generation)).rowcount
            if changed:
                db.execute('UPDATE youmi_memory_cache_partitions SET writing=0,last_error=NULL WHERE owner=?', (owner,))
            return bool(changed)

    def _error(self, owner, error):
        reason = 'busy' if isinstance(error, EmbeddingUnavailable) else 'database' if isinstance(error, sqlite3.Error) else 'io'
        try:
            with self._db() as db:
                db.execute('UPDATE youmi_memory_cache_partitions SET last_error=? WHERE owner=?', (reason, owner))
        except sqlite3.Error:
            pass  # Pending generation/writing flag still survives the failure.

    def cleanup(self, owner):
        try:
            with self._db() as db:
                registered = db.execute('SELECT 1 FROM youmi_memory_cache_partitions WHERE owner=?', (owner,)).fetchone()
            if registered is None:
                return True
            self._root()
            with _locked(self.directory(owner), timeout=0):
                return self._clean_locked(owner, self.directory(owner))
        except (OSError, sqlite3.Error, EmbeddingUnavailable) as error:
            self._error(owner, error)
            return False

    def recover(self, *, cursor='', limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid cleanup batch size')
        with self._db() as db:
            sql = ('SELECT p.owner FROM youmi_memory_cache_partitions p '
                'LEFT JOIN youmi_memory_cache_invalidations q ON q.owner=p.owner '
                'WHERE (q.owner IS NULL OR q.generation>q.cleaned_generation OR p.writing=1 OR p.last_error IS NOT NULL) '
                'AND p.owner>? ORDER BY p.owner LIMIT ?')
            owners = [row[0] for row in db.execute(sql, (cursor, limit))]
            if not owners and cursor:
                owners = [row[0] for row in db.execute(sql, ('', limit))]
        completed = sum(self.cleanup(owner) for owner in owners)
        return dict(processed=len(owners), complete=completed, cursor=owners[-1] if owners else '')

    @staticmethod
    def status(db, owner):
        row = db.execute('SELECT p.writing,p.last_error,q.generation,q.cleaned_generation '
            'FROM youmi_memory_cache_partitions p LEFT JOIN youmi_memory_cache_invalidations q ON q.owner=p.owner '
            'WHERE p.owner=?', (owner,)).fetchone()
        if row is None:
            return dict(state='not_registered', error=None)
        pending = row[2] is None or row[2] != row[3] or row[0] or row[1] is not None
        return dict(state='pending' if pending else 'complete', error=row[1])

    def search(self, corpus, rows, query, vector_scores, *, limit=4):
        self.register(corpus)
        owner, path = corpus.owner, self.directory(corpus.owner)
        try:
            self._root()
            with _locked(path):
                with self._db() as db:
                    state = self._state(db, owner)
                    status = self.status(db, owner)
                if status['state'] == 'pending':
                    if not self._clean_locked(owner, path):
                        raise EmbeddingUnavailable('memory_changed')
                if state is None or tuple(state[:2]) != corpus.version:
                    raise EmbeddingUnavailable('memory_changed')
                # Persist BEFORE touching native segments. A hard process exit
                # leaves recovery work even if no fact changed in the meantime.
                with self._db() as db:
                    db.execute('UPDATE youmi_memory_cache_partitions SET writing=1 WHERE owner=?', (owner,))
                result = (_search_locked(path, rows, query, vector_scores) if limit == 4 else
                    _search_locked(path, rows, query, vector_scores, limit=limit))
                with self._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = self._state(db, owner)
                    unchanged = current is not None and tuple(current[:2]) == corpus.version
                    if unchanged:
                        db.execute('UPDATE youmi_memory_cache_partitions SET writing=0,last_error=NULL WHERE owner=?', (owner,))
                if not unchanged:
                    self._clean_locked(owner, path)
                    raise EmbeddingUnavailable('memory_changed')
                return result
        except (OSError, sqlite3.Error) as error:
            self._error(owner, error)
            raise EmbeddingUnavailable('memory_shadow_cache_unavailable') from None
        except Exception as error:
            # A native failure also leaves writing=1. No exception text/PII is stored.
            if not isinstance(error, EmbeddingUnavailable) or str(error) != 'memory_changed':
                self._error(owner, error)
            raise
