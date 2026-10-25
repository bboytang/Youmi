"""P22 source-grounded probes; ablations analyze actual returned candidates."""

from pathlib import Path
import traceback


async def drive_retrieval(sample, ask):
    from youmi_companion.persona import CORE
    from youmi_companion.retrieval import WEIGHTS, THRESHOLD, SEMANTIC_FLOOR, RESULTS

    source_ids = []
    for source in sample['sources']:
        _, saved = await ask(source)
        found = [row for row in saved['memories'] if row['confirmed'] and row['content'] == source]
        assert len(found) == 1 and found[0]['source'], 'Actual source was not saved with provenance'
        source_ids.append(found[0]['id'])
    results, observed = [], set()
    for specification in sample['queries']:
        result = {'id': specification['id'], 'structural_pass': False, 'semantic_review': 'required'}
        try:
            answer, saved = await ask(specification['query'])
            result['response'] = answer['tts_text']
            assert answer['tts_text'].strip() and saved['job_state'] in {'committed', 'empty'}
            assert answer['input']['messages'][0]['content'].startswith(CORE)
            assert answer['input']['messages'][-1] == {'role': 'user', 'content': specification['query']}
            assert answer['diagnostics']['estimated_input'] <= answer['diagnostics']['input_limit']
            reads = answer['retrievals']
            assert len(reads) == 1 and reads[0]['query'] == specification['query']
            rag = reads[0]
            assert rag['weights'] == WEIGHTS
            candidates = rag['candidates']
            candidate_ids = {row['id'] for row in candidates}
            assert set(source_ids).issubset(candidate_ids), 'Source missing from actual candidate union'
            selected = [row['id'] for row in rag['results']]
            ablations = {}
            for component, weight in WEIGHTS.items():
                rescored = []
                for row in candidates:
                    contribution = weight * row['signals'][component]
                    if row['id'] in selected and contribution > 0:
                        observed.add(component)
                    score = row['score'] - contribution
                    if row['original_cosine'] >= SEMANTIC_FLOOR and score >= THRESHOLD:
                        rescored.append((score, row['id']))
                ablations[component] = [{'id': identifier, 'score': score}
                    for score, identifier in sorted(rescored, reverse=True)[:RESULTS]]
            result.update(expected_terms=specification['answer_terms'], selected_ids=selected,
                candidate_count=len(candidates), source_count=len(source_ids),
                expansion=rag['expansion'], ablations=ablations)
            unrelated_id = source_ids[sample['unrelated_importance_source_index']]
            unrelated = next(row for row in candidates if row['id'] == unrelated_id)
            assert unrelated['signals']['emotion'] > 0, 'Unrelated importance metadata was not actually extracted'
            assert unrelated_id not in selected
            if specification['source_index'] is None:
                assert not selected and not answer['diagnostics']['selected_fact_ids']
            else:
                target = source_ids[specification['source_index']]
                assert target in selected and target in answer['diagnostics']['selected_fact_ids']
            if specification['id'] == 'association_cue':
                assert any(row['memory_id'] == source_ids[1] for row in rag['expansion'])
            if specification['id'] == 'importance':
                target = next(row for row in candidates if row['id'] == source_ids[2])
                assert target['signals']['emotion'] > 0, 'Actual extractor supplied no importance signal'
            result['structural_pass'] = True
        except Exception as error:
            result['error_class'] = type(error).__name__
            result['error_locations'] = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                for frame in traceback.extract_tb(error.__traceback__)[-5:]]
        results.append(result)
    return {'cases': results, 'source_ids': source_ids, 'observed_components': sorted(observed),
        'all_components_observed': observed == set(WEIGHTS),
        'scope': 'Actual source-generated small corpus; ablations are analysis, not modified runtime weights; not overflow or P23 resource acceptance'}
