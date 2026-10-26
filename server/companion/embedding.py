"""The P06-selected offline Chinese encoder, loaded once by the owning index."""

import hashlib
import json
from pathlib import Path
import threading
import time
from collections import deque


MODEL = 'BAAI/bge-base-zh-v1.5'
REVISION = 'f03589ceff5aac7111bd60cfc7d497ca17ecac65'
DIMENSION = 768
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'
MODEL_SHA256 = 'dc36bd336224eaa24f2c032d9b4864d4121b72c24bbcd371813e5348ceb02e9e'
MODEL_KEY = MODEL + '/u8u8/' + MODEL_SHA256


class EmbeddingUnavailable(RuntimeError):
    """An explicit unavailable result, never a lexical or recent-memory fallback."""


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class _NativeSlots:
    """Two active native calls, four bounded FIFO waiters; no fresh-call barging."""
    def __init__(self):
        self._cond = threading.Condition()
        self._available = 2
        self._queue = deque()

    def acquire(self, timeout):
        deadline = time.monotonic() + timeout
        with self._cond:
            if self._available and not self._queue:
                self._available -= 1
                return True
            if len(self._queue) >= 4:
                return False
            ticket = object()
            self._queue.append(ticket)
            try:
                while self._queue[0] is not ticket or not self._available:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)
                self._queue.popleft()
                self._available -= 1
                self._cond.notify_all()
                return True
            finally:
                if ticket in self._queue:
                    self._queue.remove(ticket)
                    self._cond.notify_all()

    def release(self):
        with self._cond:
            if self._available >= 2:
                raise ValueError('Native slot released too many times')
            self._available += 1
            self._cond.notify_all()


class SemanticEncoder:
    model_id = MODEL_KEY
    revision = REVISION
    dimension = DIMENSION

    def __init__(self, directory):
        # No download or provider call in the voice process. Assets are built
        # from the fixed public revision and verified before the native loader.
        directory = Path(directory)
        try:
            metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
            expected = {'model': MODEL, 'revision': REVISION, 'dimension': DIMENSION,
                        'query_prefix': QUERY_PREFIX, 'max_tokens': 512, 'pooling': 'CLS',
                        'normalized': True,
                        'quantization': {'format': 'U8U8', 'per_channel': True, 'reduce_range': False}}
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise ValueError()
            if file_sha256(directory / 'model-uint8.onnx') != MODEL_SHA256:
                raise ValueError()
            tokenizer_hash = metadata.get('tokenizer_sha256')
            if not isinstance(tokenizer_hash, str) or file_sha256(directory / 'tokenizer.json') != tokenizer_hash:
                raise ValueError()
        except (OSError, ValueError, TypeError):
            raise EmbeddingUnavailable('embedding_assets_invalid') from None
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
            self._np = np
            self._tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
            self._tokenizer.enable_truncation(max_length=512)
            self._tokenizer.enable_padding()
            options = ort.SessionOptions()
            # Leave CPU capacity for foreground scans and the other services.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.add_session_config_entry('session.disable_prepacking', '1')
            self._session = ort.InferenceSession(str(directory / 'model-uint8.onnx'), options,
                                                 providers=['CPUExecutionProvider'])
            self._inputs = {item.name for item in self._session.get_inputs()}
            self._lock = threading.Lock()
            self._native_slots = _NativeSlots()
        except Exception:
            raise EmbeddingUnavailable('embedding_runtime_unavailable') from None

    def encode(self, text, query=False):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096 or type(query) is not bool:
            raise ValueError('Invalid embedding input')
        np = self._np
        if not self._native_slots.acquire(timeout=0.4):
            raise EmbeddingUnavailable('embedding_busy')
        try:
            # Tokenizer state stays serialized; each native Run owns its inputs
            # and outputs. The encoder-wide bound also covers direct Worker calls.
            with self._lock:
                tokens = self._tokenizer.encode((QUERY_PREFIX if query else '') + text)
                values = {'input_ids': np.array([tokens.ids], dtype=np.int64),
                          'attention_mask': np.array([tokens.attention_mask], dtype=np.int64),
                          'token_type_ids': np.array([tokens.type_ids], dtype=np.int64)}
            vector = self._session.run(None, {key: values[key] for key in self._inputs})[0][0, 0].astype(np.float32)
            length = float(np.linalg.norm(vector))
            if vector.shape != (DIMENSION,) or not np.isfinite(vector).all() or not 0 < length < float('inf'):
                raise ValueError()
            return vector / length
        except Exception:
            raise EmbeddingUnavailable('embedding_inference_failed') from None
        finally:
            self._native_slots.release()
