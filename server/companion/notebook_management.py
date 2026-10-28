"""Read the QQ notebook only for its currently authorized console owner."""

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .astrbot_identity import BridgeIdentity
from .authorization import PrivateAuthorizationRequired


async def read_notebook(authorization, owner, operation):
    if not isinstance(operation, dict) or set(operation) - {'kind', 'entry_id', 'before_id'}:
        raise ValueError('invalid_notebook_request')
    kind = operation.get('kind', 'important')
    entry_id, before_id = operation.get('entry_id', 0), operation.get('before_id', 0)
    if kind not in ('important', 'secret') or any(type(v) is not int or v < 0 or v > 2**63 - 1 for v in (entry_id, before_id)):
        raise ValueError('invalid_notebook_request')
    config_path = os.environ.get('YOUMI_ASTRBOT_CONFIG_FILE')
    db_path = os.environ.get('YOUMI_NOTEBOOK_DB')
    if not config_path or not db_path:
        raise PrivateAuthorizationRequired()
    bridge = json.loads(Path(config_path).read_text(encoding='utf-8'))
    identity = BridgeIdentity(authorization, bridge['bindings'])
    matches = []
    for binding in bridge['bindings']:
        conversation = binding['conversation_key']
        if not isinstance(conversation, str) or not conversation.startswith('qq:private:'):
            continue
        source = {'bot_uin': binding['bot_uin'], 'sender_uin': conversation[len('qq:private:'):], 'conversation_key': conversation}
        try:
            scope = await identity.resolve(source)
        except PrivateAuthorizationRequired:
            continue
        if scope.owner == owner:
            matches.append((source, scope))
    if len(matches) != 1:
        raise PrivateAuthorizationRequired()
    source, scope = matches[0]
    notebook_scope = f"qq:{source['bot_uin']}:{source['sender_uin']}"

    def query():
        uri = Path(db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            if entry_id:
                row = db.execute('SELECT id,created_at,updated_at,kind,title,content FROM entries WHERE scope=? AND kind=? AND id=?',
                                 (notebook_scope, kind, entry_id)).fetchone()
                return {'entry': dict(row) if row else None}
            rows = db.execute('SELECT id,created_at,updated_at,kind,title FROM entries WHERE scope=? AND kind=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 21',
                              (notebook_scope, kind, before_id, before_id)).fetchall()
            return {'entries': [dict(row) for row in rows[:20]], 'next_before_id': rows[19]['id'] if len(rows) > 20 else None}

    try:
        result = await asyncio.to_thread(query)
    except (OSError, sqlite3.Error):
        raise PrivateAuthorizationRequired() from None
    if await identity.resolve(source) != scope:
        raise PrivateAuthorizationRequired()
    return result
