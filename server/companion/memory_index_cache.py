"""Native index primitives; standalone search is synthetic evaluation only.

Standalone partitions reconcile on the next query, not at fact deletion time.
The separately opt-in managed coordinator owns registration and cleanup while
reusing the stable lock and private _search_locked implementation below.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from .embedding import EmbeddingUnavailable


FORMAT = 1
MANIFEST_LIMIT = 4 * 1024 * 1024


def partition(root, database, owner, persona):
    key = json.dumps([str(Path(database).resolve()), owner, persona], ensure_ascii=False)
    return Path(root) / hashlib.sha256(key.encode()).hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


@contextmanager
def _locked(path, *, timeout=.4):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink():
        raise OSError('Cache partition cannot be a symlink')
    os.chmod(path, 0o700)
    descriptor = os.open(path / 'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise EmbeddingUnavailable('memory_shadow_cache_busy') from None
                time.sleep(.01)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _manifest(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MANIFEST_LIMIT + 1)
        if len(raw) > MANIFEST_LIMIT:
            return None
        value = json.loads(raw)
        hashes = value['hashes']
        if (set(value) != {'format', 'hashes'} or type(value['format']) is not int or value['format'] != FORMAT
                or not isinstance(hashes, dict) or len(hashes) > 10000
                or any(not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(digest, str)
                    or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                    for key, digest in hashes.items())):
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def search(path, rows, query, vector_scores, *, limit=4):
    """A complete authorized snapshot reconciles the partition before any search."""
    path = Path(path)
    with _locked(path):
        return _search_locked(path, rows, query, vector_scores, limit=limit)


def _search_locked(path, rows, query, vector_scores, *, limit=4):
    """Caller must hold the stable partition writer.lock through search."""
    from .memory_search import YoumiBM25Search
    path = Path(path)
    current = {row['id']: hashlib.sha256(json.dumps([row['judgment'], row['tags']],
        ensure_ascii=False).encode()).hexdigest() for row in rows}
    manifest_path, dirty, index_path = path / 'manifest.json', path / 'dirty.json', path / 'index'
    if index_path.is_symlink() or any(child.is_symlink() for child in index_path.rglob('*')):
        raise OSError('Cache index cannot contain symlinks')
    manifest = _manifest(manifest_path)
    previous = manifest['hashes'] if manifest is not None else {}
    reset = dirty.exists() or manifest is None or not index_path.is_dir()
    # Replacing/removing facts rebuilds from scratch, dropping historical
    # segment files as well as logical documents. New facts can append.
    reset = reset or any(current.get(key) != digest for key, digest in previous.items())
    engine = None
    if not reset:
        try:
            engine = YoumiBM25Search(index_path)
            if engine._index.searcher().num_docs != len(previous):
                reset = True
        except Exception:
            reset = True
    if reset or current != previous:
        _replace_json(dirty, {'format': FORMAT})
        if reset:
            engine = None  # Release reader before removing its segment files.
            if index_path.exists():
                shutil.rmtree(index_path)
            index_path.mkdir(mode=0o700)
            engine = YoumiBM25Search(index_path)
            engine.rebuild(rows)
        else:
            engine.append([row for row in rows if row['id'] not in previous])
        _replace_json(manifest_path, {'format': FORMAT, 'hashes': current})
        dirty.unlink()
        _sync_directory(path)
    else:
        engine._index.reload()
    return engine.search(query, vector_scores) if limit == 4 else engine.search(query, vector_scores, limit=limit)
