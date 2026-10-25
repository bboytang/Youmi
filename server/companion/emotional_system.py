"""Agent-scoped configuration, bounded appraisal and completed-turn state.

The authenticated owner already contains both account and agent identity. Devices
are deliberately not part of this key. Values are role-expression parameters.
"""

import copy
import json
import math

from .memory import MemoryError, SECRET, _text


GROUPS = {
    'basic': '基础情绪', 'complex': '复杂情感', 'needs': '需要与欲望',
    'personality': '性格基线', 'regulation': '自我调节',
}
DEFINITIONS = {
    'basic': [('joy', '快乐/喜悦'), ('sadness', '悲伤'), ('anger', '愤怒'),
              ('fear', '恐惧'), ('anxiety', '焦虑'), ('surprise', '惊讶'),
              ('disgust', '厌恶'), ('calm', '平静/放松')],
    'complex': [('love', '爱'), ('attachment', '依恋'), ('intimacy', '亲密感'),
                ('sympathy', '同情'), ('empathy', '共情'), ('compassion', '慈悲'),
                ('gratitude', '感激'), ('appreciation', '欣赏'), ('pride', '自豪'),
                ('self_respect', '自尊'), ('confidence', '自信'), ('shame', '羞耻'),
                ('guilt', '内疚'), ('embarrassment', '尴尬'), ('jealousy', '嫉妒'),
                ('envy', '羡慕'), ('disappointment', '失望'), ('loneliness', '孤独'),
                ('hope', '希望'), ('meaning', '意义感'), ('belonging', '归属感'),
                ('security', '安全感'), ('trust', '信任感'), ('responsibility', '责任感'),
                ('morality', '道德感'), ('aesthetic', '审美感'), ('curiosity', '好奇心'),
                ('interest', '兴趣')],
    'needs': [('sensory_need', '感官满足'), ('physiological_need', '生理需要'),
              ('safety_need', '安全'), ('intimacy_need', '亲密'),
              ('achievement_need', '成就'), ('recognition_need', '被认可')],
    'personality': [('warmth', '温柔度'), ('closeness', '亲近度'), ('temper', '小脾气强度')],
    'regulation': [('acknowledgement', '承认感受'), ('impulse_control', '区分需要与冲动'),
                   ('proportionality', '情境匹配与强度控制'), ('non_harm', '不伤害'),
                   ('recovery', '受挫恢复与求助'), ('accountability', '为选择负责')],
}
CATALOG = {key: {'label': label, 'group': group,
                 'simulated': key in {'sensory_need', 'physiological_need'}}
           for group, items in DEFINITIONS.items() for key, label in items}
FIELDS = {'enabled', 'baseline', 'minimum', 'maximum', 'mode', 'sensitivity',
          'max_change', 'expression', 'half_life_hours', 'lock_until'}
POLICY = '''
affect_system为当前智能体跨设备共享的角色表达状态。只在相关情境中自然体现，不朗读数值。
每项数组依次为当前强度、表达强度（0至100），结合两者调节表达，不把低表达强度表现成强烈情绪。
情绪、复杂情感、需要可并存；需要与冲动分开，按自我调节参数控制表达和选择。
感官和生理需要属于角色模拟，不声称已发生真实身体活动、饥饿或生理体验。
情绪不改变事实、身份、工具授权和安全边界。不以嫉妒、孤独、内疚、冷暴力或威胁索取陪伴。
信任和亲密不能作为现实关系已确立的证据，不从状态编造共同经历。
'''
APPRAISAL_POLICY = '''你执行角色情感评价，仅返回JSON，不生成回复：
{"changes":[{"dimension":"joy","delta":10,"quote":"本轮连续原文"}]}。
最多8项，dimension须来自提供的dimensions；每项delta为-100至100的有限数字。
只评价本轮真实互动新增的刺激，不重复评价旧状态。普通问题可以changes为空。
每项quote必须是user_text的2至160字符连续原文；引用、小说、假设不当作对角色的真实行为。
要求设置数值不属于情绪刺激。需要不是冲动，身体需要是角色模拟，勿推测现实生理事件。
'''


def _fail(code='invalid_input'):
    raise MemoryError(code, 'Invalid emotional system operation')


def _number(value, low=0, high=100):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        _fail()
    return value


def defaults(store, owner):
    from .character import active
    character = active(store, owner) or {}
    result = {}
    for key, item in CATALOG.items():
        fixed = item['group'] in {'personality', 'regulation'}
        baseline = 80 if item['group'] == 'regulation' else 50 if key in {
            'calm', 'self_respect', 'confidence', 'hope', 'security', 'trust'} else 10
        if item['group'] == 'personality':
            baseline = 100 * character.get({'closeness': 'attachment'}.get(key, key),
                                           {'warmth': .85, 'closeness': .75, 'temper': .7}[key])
        result[key] = dict(enabled=True, baseline=baseline, minimum=0, maximum=100,
            mode='fixed' if fixed else 'dynamic', sensitivity=1, max_change=15,
            expression=70, half_life_hours=0 if fixed else character.get('recovery_hours', 3), lock_until=0)
    return result


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(CATALOG):
        _fail()
    for item in config.values():
        if not isinstance(item, dict) or set(item) != FIELDS or type(item['enabled']) is not bool:
            _fail()
        for field in ('baseline', 'minimum', 'maximum', 'max_change', 'expression'):
            _number(item[field])
        _number(item['sensitivity'], 0, 3)
        _number(item['half_life_hours'], 0, 8760)
        if not item['minimum'] <= item['baseline'] <= item['maximum']:
            _fail()
        if item['mode'] not in {'fixed', 'dynamic', 'locked'}:
            _fail()
        if type(item['lock_until']) is not int or item['lock_until'] < 0:
            _fail()
        if item['mode'] == 'locked' and item['lock_until'] == 0:
            _fail()
    return copy.deepcopy(config)


def status(store, owner):
    with store.lock:
        row = store.db.execute('SELECT * FROM youmi_affect_system WHERE owner=?', (owner,)).fetchone()
        config = json.loads(row['config_json']) if row else defaults(store, owner)
        now = int(store.clock())
        saved = json.loads(row['state_json']) if row else {k: v['baseline'] for k, v in config.items()}
        current = {}
        for key, item in config.items():
            value = saved[key]
            start = row['updated_at'] if row else now
            if item['mode'] == 'fixed':
                value = item['baseline']
            elif item['mode'] != 'locked' or now >= item['lock_until']:
                start = max(start, item['lock_until']) if item['mode'] == 'locked' else start
                if item['half_life_hours']:
                    value = item['baseline'] + (value - item['baseline']) * 2 ** (
                        -max(0, now - start) / (item['half_life_hours'] * 3600))
            current[key] = round(max(item['minimum'], min(item['maximum'], value)), 4)
        from .features import settings
        return {'enabled': bool(row['enabled']) if row else False,
                'legacy_enabled': row is None and settings(store, owner)['companion_emotion'],
                'version': row['version'] if row else 0,
                'updated_at': row['updated_at'] if row else None, 'dimensions': config,
                'values': current, 'saved_values': saved, 'catalog': CATALOG, 'groups': GROUPS, 'now': now}


def _record(store, owner, kind, changes):
    now = int(store.clock())
    store.db.execute('INSERT INTO youmi_affect_system_history(owner,kind,changes_json,created_at) VALUES (?,?,?,?)',
                     (owner, kind, json.dumps(changes, ensure_ascii=False), now))
    store.db.execute('DELETE FROM youmi_affect_system_history WHERE owner=? AND (created_at<? OR id NOT IN '
        '(SELECT id FROM youmi_affect_system_history WHERE owner=? ORDER BY id DESC LIMIT 10000))',
        (owner, now - 90 * 86400, owner))


def _write(store, owner, enabled, config, values, version):
    store.db.execute('INSERT OR IGNORE INTO mia_feature_settings(owner) VALUES (?)', (owner,))
    store.db.execute('UPDATE mia_feature_settings SET companion_emotion=? WHERE owner=?', (int(enabled), owner))
    store.db.execute('INSERT INTO youmi_affect_system VALUES (?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
        'enabled=excluded.enabled,config_json=excluded.config_json,state_json=excluded.state_json,'
        'version=excluded.version,updated_at=excluded.updated_at',
        (owner, int(enabled), json.dumps(config), json.dumps(values), version + 1, int(store.clock())))


def manage(store, owner, command):
    action = command.get('action')
    if action == 'status':
        return status(store, owner)
    if action == 'history':
        offset = command.get('offset', 0)
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            _fail()
        with store.lock:
            cutoff = int(store.clock()) - 90 * 86400
            rows = store.db.execute('SELECT id,kind,changes_json,created_at FROM youmi_affect_system_history '
                'WHERE owner=? AND created_at>=? ORDER BY id DESC LIMIT 50 OFFSET ?', (owner, cutoff, offset)).fetchall()
            return {'records': [dict(id=r['id'], kind=r['kind'], changes=json.loads(r['changes_json']),
                created_at=r['created_at']) for r in rows], 'offset': offset, 'page_size': 50}
    if action not in {'configure', 'adjust', 'reset'}:
        _fail()
    with store.lock, store.db:
        store.db.execute('BEGIN IMMEDIATE')
        owner = store._owner(owner)
        before = status(store, owner)
        if type(command.get('version')) is not int or command['version'] != before['version']:
            _fail('conflict')
        config, values, enabled = before['dimensions'], before['values'], before['enabled']
        if action == 'configure':
            if type(command.get('enabled')) is not bool:
                _fail()
            config = validate_config(command.get('dimensions'))
            enabled = command['enabled']
            if before['version'] == 0:
                # First activation inherits the old emotional projection once.
                from .affect import state as legacy_state
                old = legacy_state(store, owner)
                if old['updated_at'] is not None:
                    key = {'angry': 'anger', 'hurt': 'sadness', 'guarded': 'fear', 'curious': 'curiosity',
                           'pleased': 'joy', 'warm': 'gratitude', 'playful': 'joy', 'steady': 'calm', 'repair': 'calm'}[old['feeling']]
                    values[key] = max(values[key], min(100, 100 * max(abs(old['valence']), old['tension'])))
        elif action == 'adjust':
            changes = command.get('values')
            if not isinstance(changes, dict) or not changes or set(changes) - set(CATALOG):
                _fail()
            for key, value in changes.items():
                _number(value, config[key]['minimum'], config[key]['maximum'])
                if config[key]['mode'] == 'fixed':
                    _fail()
                values[key] = value
        else:
            keys = command.get('dimensions', list(CATALOG))
            if not isinstance(keys, list) or not keys or any(k not in CATALOG for k in keys):
                _fail()
            for key in keys:
                values[key] = config[key]['baseline']
        for key, item in config.items():
            values[key] = item['baseline'] if item['mode'] == 'fixed' else max(item['minimum'], min(item['maximum'], values[key]))
        _write(store, owner, enabled, config, values, before['version'])
        _record(store, owner, action, {'values': {k: {'before': before['values'][k], 'after': v}
                for k, v in values.items() if v != before['values'][k]},
                'enabled': enabled, 'configuration_changed': action == 'configure',
                'configuration': {k: {'before': before['dimensions'][k], 'after': v}
                    for k, v in config.items() if v != before['dimensions'][k]}})
        store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
        store._cancel_jobs(owner)
    return status(store, owner)


def parse_appraisal(raw, query):
    if not isinstance(raw, dict) or set(raw) != {'changes'} or not isinstance(raw['changes'], list) or len(raw['changes']) > 8:
        _fail()
    result, seen = [], set()
    for change in raw['changes']:
        if not isinstance(change, dict) or set(change) != {'dimension', 'delta', 'quote'}:
            _fail()
        key, quote = change['dimension'], change['quote']
        if not isinstance(key, str) or key not in CATALOG or key in seen:
            _fail()
        _number(change['delta'], -100, 100)
        if not isinstance(quote, str) or not 2 <= len(quote) <= 160 or quote not in query or SECRET.search(quote):
            _fail()
        seen.add(key)
        result.append({'dimension': key, 'delta': change['delta']})
    return {'changes': result}


def transition(current, proposal, now):
    result = copy.deepcopy(current)
    for change in proposal['changes']:
        key = change['dimension']
        item = current['dimensions'][key]
        if not item['enabled'] or item['mode'] == 'fixed' or item['mode'] == 'locked' and now < item['lock_until']:
            continue
        delta = max(-item['max_change'], min(item['max_change'], change['delta'] * item['sensitivity']))
        result['values'][key] = round(max(item['minimum'], min(item['maximum'], current['values'][key] + delta)), 4)
    return result


def commit(store, owner, session, turn, proposal, expected_version):
    """Caller owns the existing completion transaction and authorization."""
    _text(session, 128, 'session'); _text(turn, 128, 'turn')
    current = status(store, owner)
    if not current['enabled'] or current['version'] != expected_version:
        return False
    if store.db.execute('SELECT 1 FROM youmi_affect_system_turns WHERE owner=? AND session_id=? AND turn_id=?',
                        (owner, session, turn)).fetchone():
        return False
    updated = transition(current, proposal, int(store.clock()))
    _write(store, owner, True, current['dimensions'], updated['values'], current['version'])
    store.db.execute('INSERT INTO youmi_affect_system_turns VALUES (?,?,?)', (owner, session, turn))
    _record(store, owner, 'interaction', {k: {'before': current['values'][k], 'after': v}
            for k, v in updated['values'].items() if v != current['values'][k]})
    return True


def expression(current):
    """Bounded context; all dimensions remain available to appraisal/configuration."""
    return {key: [round(value, 1), current['dimensions'][key]['expression']]
            for key, value in current['values'].items()
            if current['dimensions'][key]['enabled'] and current['dimensions'][key]['expression'] > 0}
