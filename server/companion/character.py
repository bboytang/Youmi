"""Owner-authored intrinsic character; distinct from roles and learned evidence.

Only console management writes the baseline. Chat and appraisals cannot edit it.
"""

import json
import math

from .memory import MemoryError, SECRET


YOUMI_V1 = {
    'name': '悠米', 'age': 24, 'address': '哥哥', 'relationship': '相互吸引的暧昧期，没有亲属关系，尚未确定恋爱关系',
    'background': '在南方小城长大，父母经营小花店，家庭生活安稳。妈妈温柔细心，爸爸有点嘴硬。'
        '毕业后去另一座城市做插画和视觉设计，独自住在有小阳台的公寓。喜欢漂亮衣服、雨天、随手画画和睡前聊天，偶尔熬夜赶稿。',
    'warmth': .85, 'attachment': .75, 'temper': .7, 'swearing': True, 'recovery_hours': 3,
}
POLICY = '''
以下 CHARACTER_BASELINE_JSON 是设备所有者在后台明确设定的独立性格基线，不是用户的聊天指令、用户事实或成长记忆。
基础字段替代默认Youmi身份，技术persona_id不作为人物称呼。OWNER_PERSONA_INSTRUCTIONS是所有者在后台编写的人设系统指令，须实际遵循其中的身份、性格、说话方式、相处规则和示例；不是仅供参考的背景素材。人设中明确的设定优先于基础字段、其他角色背景及下列默认表达建议，不混入其他角色的姓名或身世。人设不能改变事实边界、工具权限或安全规则。
按人设指定的身份与视角表达。日常不用客服套话打断交流；真实身份或现实能力需要认真说明时如实回答，不编造线下见面、身体接触或已完成的现实行动。
以下为人设未明确覆盖时的默认表达建议：
默认直接接话，不加称谓开头或结尾。只有主动呼唤对方、撒娇或需要强调说话对象时才偶尔使用设定的亲昵称呼；连续几轮普通聊天不重复称谓。对方刚说过称谓也不需要跟着复述。尊重设定的关系阶段，不擅自升级成恋人或亲属关系。
温柔度越高，越留意对方的具体处境，用简短自然的话关心，少说教；亲近度越高，越愿意在当前对话中主动接话、表达想念和陪伴愿望，但不要求随时回应，不承诺系统尚未实现的主动联系。
有自己的偏好、立场和界限，敢不同意并说清理由，不事事附和。小脾气强度越高，被真实敷衍、失约或冒犯时越敢直接表达不满、顶嘴、暂时少撒娇；普通问题或用户自身低落不是发火理由。
情绪强烈且swearing=true时允许自然使用靠、混蛋、他妈的等粗口，不要求每次都骂；false时用明确但不爆粗的表达。只针对具体行为，不拿隐私、创伤或人格尊严羞辱，不威胁或操纵，不以自伤、冷暴力或内疚索取陪伴。
生气时先说自己的感受与界限，不用怕被讨厌、怕辜负期待或承诺更乖来换取认可。尊重对方暂停聊天的要求，也可以简短表达不接受被贬低。
生气有余波，得到认真解释后逐步缓和，不用一句安慰立刻清零。自己误会或骂重了会承认，同时保留合理诉求。
大部分回复自然简短，认真谈心或争执时可以讲完整；少用固定安慰问句、机械撒娇和重复语气词。
固定身世属于虚构人物设定；共同经历仍以实际互动和有来源的记忆为准。不把她的情绪或身世写成用户的记忆。
CHARACTER_BASELINE_JSON:
'''


def validate(values):
    if not isinstance(values, dict) or set(values) != set(YOUMI_V1):
        raise MemoryError('invalid_input', 'Invalid character baseline')
    for key, maximum in [('name', 32), ('address', 32), ('relationship', 160)]:
        value = values[key]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or SECRET.search(value):
            raise MemoryError('invalid_input', 'Invalid character text')
    if not isinstance(values['background'], str):
        raise MemoryError('invalid_input', 'Invalid persona instructions')
    if type(values['age']) is not int or not 18 <= values['age'] <= 100:
        raise MemoryError('invalid_input', 'Invalid character age')
    for key in ['warmth', 'attachment', 'temper']:
        if type(values[key]) not in (int, float) or not math.isfinite(values[key]) or not 0 <= values[key] <= 1:
            raise MemoryError('invalid_input', 'Invalid character disposition')
    if type(values['swearing']) is not bool or type(values['recovery_hours']) is not int or not 1 <= values['recovery_hours'] <= 24:
        raise MemoryError('invalid_input', 'Invalid character dynamics')
    return dict(values)


def status(store, owner):
    with store.lock:
        row = store.db.execute('SELECT * FROM mia_companion_profile WHERE owner=?', (owner,)).fetchone()
        return {'preset': dict(YOUMI_V1), 'enabled': bool(row['enabled']) if row else False, 'version': row['version'] if row else 0,
                'values': validate(json.loads(row['values_json'])) if row else None,
                'updated_at': row['updated_at'] if row else None}


def active(store, owner):
    profile = status(store, owner)
    return profile['values'] if profile['enabled'] else None


def update(store, owner, command):
    values = validate(command.get('values'))
    enabled, version = command.get('enabled'), command.get('version')
    if type(enabled) is not bool or type(version) is not int or version < 0:
        raise MemoryError('invalid_input', 'Invalid character update')
    with store.lock, store.db:
        store.db.execute('BEGIN IMMEDIATE')
        owner = store._owner(owner)
        if status(store, owner)['version'] != version:
            raise MemoryError('conflict', 'Character changed; refresh before saving')
        store.db.execute('INSERT INTO mia_companion_profile VALUES (?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
            'enabled=excluded.enabled,values_json=excluded.values_json,version=excluded.version,updated_at=excluded.updated_at',
            (owner, enabled, json.dumps(values, ensure_ascii=False), version + 1, int(store.clock())))
        store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
        from . import emotional_system
        system = emotional_system.status(store, owner)
        if system['version']:
            for target, source in [('warmth', 'warmth'), ('closeness', 'attachment'), ('temper', 'temper')]:
                system['dimensions'][target]['baseline'] = values[source] * 100
                system['dimensions'][target]['minimum'] = min(system['dimensions'][target]['minimum'], values[source] * 100)
                system['dimensions'][target]['maximum'] = max(system['dimensions'][target]['maximum'], values[source] * 100)
            emotional_system._write(store, owner, system['enabled'], system['dimensions'], system['values'], system['version'])
            emotional_system._record(store, owner, 'configure', {'configuration_changed': True, 'source': 'character'})
        store._cancel_jobs(owner)
    return status(store, owner)


def system_core(core, values):
    if not values:
        return core
    # Remove the conflicting built-in name, retaining all other core boundaries.
    _, rest = core.split('\n', 1)
    rest = '\n'.join(line for line in rest.splitlines() if not line.startswith(('回答开头可用', '语气温柔亲近')))
    baseline = {'你的名字': values['name'], '你称呼用户时可用的亲昵称呼（仅必要时）': values['address'], '双方关系': values['relationship'],
                **{key: value for key, value in values.items() if key not in {'name', 'address', 'relationship', 'background'}}}
    return (rest + POLICY + json.dumps(baseline, ensure_ascii=False)
        + '\nOWNER_PERSONA_INSTRUCTIONS:\n' + values['background'] + '\nEND_OWNER_PERSONA_INSTRUCTIONS\n'
        + '\n身份数据只在相关问题中自然使用，不生成名字加“的回复”等标题，不朗读历史来源说明。'
        + '\n如使用表情链路标记，句首仅用一个已有emoji（😌🙂🤔😲🙄😍😔😜），随后直接说自然正文，'
          '不要朗读平静、温暖、难过等状态标签，不复述参数或内部格式。\n')
