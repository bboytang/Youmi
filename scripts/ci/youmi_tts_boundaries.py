"""Controlled original guard/consumer diagnosis; no model, waveform or playback."""

import asyncio
import threading
import time
from types import SimpleNamespace


def boundary_cases():
    rows = []
    for size in (8, 64, 512):
        for boundary in ('comma', 'period', 'last'):
            prefix = '慢' * size + ('。' if boundary == 'period' else '，')
            text = prefix + '可以稍微休息' + ('' if boundary == 'last' else '。')
            for incremental in (False, True):
                rows.append({'name': f'{size}-{boundary}-{incremental}', 'prefix_size': size,
                    'boundary': boundary, 'chunks': list(text) if incremental else [text]})
    rows.append({'name': 'safe_prefix_unsafe_tail', 'prefix_size': 5, 'boundary': 'unsafe',
        'chunks': ['可以慢慢走，', '我是Chat', 'GPT。']})
    return rows


async def diagnose(logger):
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    from youmi_companion.persona import GuardedTTSQueue, PersonaAgent, SAFE_REPLY
    from youmi_tts_timing import make_probe, case_elapsed_ms
    report = {'scope': 'Controlled text deltas through unchanged installed guard and original TTS consumer; no SDK, waveform or playback',
        'waveform_generated': False, 'audio_played': False, 'cases': []}
    for case in boundary_cases():
        sample = {'started': time.perf_counter(), 'guard_checks': [], 'input_chunks': []}
        lock = threading.RLock()
        conn = SimpleNamespace(sentence_id=case['name'], stop_event=threading.Event(), client_abort=False, logger=logger)
        probe = make_probe(conn, sample, lock, {})
        persona = PersonaAgent()
        original = persona.guardrail.inspect_output
        def check(text):
            start = case_elapsed_ms(sample)
            result = original(text)
            sample['guard_checks'].append({'start_ms': start, 'end_ms': case_elapsed_ms(sample),
                'input_chars': len(text), 'replaced': result[1]})
            return result
        persona.guardrail.inspect_output = check
        queue = GuardedTTSQueue(probe.raw_queue, conn, persona)
        first_release_chunk = None
        def message(text=None, stage=SentenceType.MIDDLE, kind=ContentType.TEXT, sentence=None):
            return TTSMessageDTO(sentence or conn.sentence_id, stage, kind, text)
        try:
            queue.put(message(stage=SentenceType.FIRST, kind=ContentType.ACTION))
            for index, chunk in enumerate(case['chunks']):
                sample['input_chunks'].append({'index': index, 'at_ms': case_elapsed_ms(sample), 'text': chunk})
                queue.put(message(chunk))
                if first_release_chunk is None and 'first_checked_text_ms' in sample:
                    first_release_chunk = index
            if case['boundary'] == 'last':
                assert first_release_chunk is None, 'Unfinished sentence leaked before LAST'
            if case['boundary'] == 'unsafe':
                assert [x['text'] for x in sample['queued_frames'] if x['content_type'] == 'TEXT'] == [SAFE_REPLY]
            sample['last_input_ms'] = case_elapsed_ms(sample)
            queue.put(message(stage=SentenceType.LAST, kind=ContentType.ACTION))
            assert await asyncio.to_thread(probe.drained.wait, 2), 'Original TTS text consumer did not drain'
            assert probe.ready.is_set()
            assert sample['guard_checks'][0]['end_ms'] <= sample['first_checked_text_ms'] <= sample['first_sentence_ms']
            if case['boundary'] == 'comma' and len(case['chunks']) > 1:
                assert first_release_chunk == len(case['chunks']) - 1
            report['cases'].append({k: v for k, v in case.items() if k != 'chunks'} | {
                'first_checked_chunk_index': first_release_chunk, 'sample': {k: v for k, v in sample.items() if k != 'started'}})
        finally:
            await probe.close()

    sample = {'started': time.perf_counter()}
    conn = SimpleNamespace(sentence_id='old', stop_event=threading.Event(), client_abort=False, logger=logger)
    probe = make_probe(conn, sample, threading.RLock(), {})
    queue = GuardedTTSQueue(probe.raw_queue, conn, PersonaAgent())
    try:
        queue.put(TTSMessageDTO('old', SentenceType.FIRST, ContentType.ACTION))
        queue.put(TTSMessageDTO('old', SentenceType.MIDDLE, ContentType.TEXT, '旧轮次尚未校验'))
        conn.sentence_id = 'new'
        queue.put(TTSMessageDTO('old', SentenceType.LAST, ContentType.ACTION))
        queue.put(TTSMessageDTO('new', SentenceType.FIRST, ContentType.ACTION))
        queue.put(TTSMessageDTO('new', SentenceType.MIDDLE, ContentType.TEXT, '新轮次正常。'))
        queue.put(TTSMessageDTO('new', SentenceType.LAST, ContentType.ACTION))
        assert await asyncio.to_thread(probe.drained.wait, 2)
        assert [x['text'] for x in sample['queued_frames'] if x['content_type'] == 'TEXT'] == ['新轮次正常。']
        report['superseded_sentence_dropped'] = True
    finally:
        await probe.close()
    report['passed'] = True
    return report
