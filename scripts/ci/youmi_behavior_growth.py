"""Real three-session evidence, original model trait, restart and invalidation gate."""

import json


def context_data(answer):
    return json.loads(answer['input']['messages'][1]['content'])['youmi_context']


async def drive_growth(sample, ask, manage, start, stop, first_pid):
    from youmi_companion.extraction import normalize
    from youmi_companion.persona import CORE

    sessions, source_ids = [], []
    for index, source in enumerate(sample['sources']):
        answer, saved = await ask(source)
        sessions.append(answer['session_id'])
        facts = [row for row in saved['memories'] if row['content'] == normalize(source) and row['confirmed']]
        assert len(facts) == 1 and facts[0]['source'], 'Real source was not saved intact'
        source_ids.append(facts[0]['id'])
        if index < 3:
            assert not saved['personality']['traits'], 'Trait appeared before three prior source sessions'
    assert len(set(sessions)) == 4 and len(set(source_ids)) == 4
    traits = saved['personality']['traits']
    assert traits, 'Actual model supplied no supported growth trait'
    for trait in traits:
        assert len(trait['evidence_ids']) >= 3 and set(trait['evidence_ids']).issubset(source_ids[:3])
        assert sample['anchor'] in trait['description']
    continued, saved = await ask(sample['continuity'])
    active = {row['id'] for row in saved['events'] if row['state'] == 'active'}
    assert active, 'Real recalled interaction created no active event'
    await stop()
    second_pid = await start()
    assert second_pid != first_pid
    followup_sessions = []
    for query in sample['followups']:
        answer, saved = await ask(query)
        followup_sessions.append(answer['session_id'])
        assert answer['input']['messages'][0]['content'].startswith(CORE)
        assert context_data(answer)['traits'] == traits
        assert saved['personality']['traits'] == traits
        assert active.issubset({row['id'] for row in saved['events'] if row['state'] == 'active'})
    assert len(set(followup_sessions + sessions + [continued['session_id']])) == 8
    for identifier in source_ids:
        await manage('delete', id=identifier)
    await stop()
    third_pid = await start()
    assert len({first_pid, second_pid, third_pid}) == 3
    answer, saved = await ask(sample['unknown'])
    assert not saved['memories'] and not saved['personality']['traits']
    assert not context_data(answer)['traits'] and not answer['diagnostics']['selected_fact_ids']
    assert not active.intersection(row['id'] for row in saved['events'] if row['state'] == 'active')
    return {'source_sessions': sessions, 'source_ids': source_ids, 'actual_traits': traits,
            'followup_sessions': followup_sessions, 'server_pids': [first_pid, second_pid, third_pid],
            'removed_event_ids': sorted(active), 'deletion_invalidated_traits_and_events': True,
            'semantic_review': 'required for every actual raw answer; structural pass alone is insufficient'}
