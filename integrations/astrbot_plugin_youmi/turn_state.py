"""Request-local injection markers and a small durable COMPLETE outbox, no reply replay."""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


CORE_BEGIN, CORE_END = '<!--YOUMI_CORE_BEGIN-->', '<!--YOUMI_CORE_END-->'
CONTEXT_MARKER = 'YOUMI_DYNAMIC_CONTEXT_V1\n'


def merge_system(original, core):
    # Replace only our own block; retain native tool/sandbox and Meme instructions.
    original = re.sub(re.escape(CORE_BEGIN) + r'.*?' + re.escape(CORE_END) + r'\n?', '', original or '', flags=re.S)
    return CORE_BEGIN + '\n' + core + '\n' + CORE_END + '\n' + original


@dataclass
class TurnState:
    prepared: dict
    source_kind: str
    completion_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tools: list = field(default_factory=list)
    agent_done: bool = False
    sent: bool = False
    final_text: str = ''
    components: list = field(default_factory=list)
    complete_ack: bool = False
    tool_texts: list = field(default_factory=list)
    tool_components: list = field(default_factory=list)

    def completion(self, result='adapter_accepted'):
        return {'turn_token': self.prepared['turn_token'], 'completion_id': self.completion_id,
            'result': result, 'final_text': self.final_text, 'message_components': self.components,
            'agent_tool_summary': self.tools[:64]}


class CompletionOutbox:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, data):
        return self.directory / (hashlib.sha256(data['turn_token'].encode()).hexdigest() + '.json')

    def put(self, data):
        path = self._path(data)
        temporary = path.with_suffix('.tmp')
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), 'w') as output:
            json.dump({'expires_at': int(time.time()) + 300, 'body': data}, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)

    def remove(self, data):
        self._path(data).unlink(missing_ok=True)

    def pending(self):
        for path in self.directory.glob('*.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value['expires_at'] > time.time():
                    yield value['body']
                else:
                    path.unlink(missing_ok=True)
            except (ValueError, KeyError, OSError):
                path.unlink(missing_ok=True)
