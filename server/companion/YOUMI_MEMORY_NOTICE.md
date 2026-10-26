# Youmi memory search attribution

Upstream: https://github.com/kawayiYokami/astrbot_plugin_angel_memory
Author: kawayiYokami and upstream contributors.
Version: 1.6.14. Commit: 17454a12669c9a7f0f2e2fbe6a424f6f8f4fffe5.

`memory_search.py` contains adapted portions of the following GPLv3 sources:

- `llm_memory/components/note_chunk_search.py`: CJK normalization, tokenization and query helpers.
- `llm_memory/components/bm25_retriever.py`: BM25 normalization, staged search design and reciprocal rank fusion.

The source was fetched from the pinned GitHub commit and compared with the local research snapshot before extraction. Local source SHA256 (the research snapshot may differ in final newlines):

- `LICENSE`: `514246917be93e7c682d717617e528bda3011845443669c1b12f6bac43ade060`
- `llm_memory/components/bm25_retriever.py`: `20a9daa1c601e6cf505f375c1cfd274009ff85889b9c7eca62af7ced7a3f9dce`
- `llm_memory/components/note_chunk_search.py`: `a94050f8d92c81cd77751fce0b094e40270c26d189d6a33c484092d31a6f2976`

Modifications by Youmi contributors, 2026-10-25:

- Remove AstrBot imports and persistent index directory management.
- Use a per-call in-memory Tantivy index built from one authorized canonical snapshot.
- Limit the writer to one thread and a 15 MB heap; explicitly reload after commit.
- Let query/index errors propagate, so an outage is not reported as no match.
- Permit already-qualified vector-only candidates when BM25 has no hits.
- Break equal fused scores deterministically using the memory ID.
- Expose no original SQLite writer, public scope fallback, external reranker or model provider.

The copied/adapted code in `memory_search.py` is supplied under GPL version 3; the complete upstream license is included as `YOUMI_MEMORY_LICENSE`. This notice does not relicense that code as MIT. Preserve this notice, the license and source when distributing it. Confirm the applicable combined-work and distribution obligations before a release; shadow support is not production enablement.

Additional modifications, 2026-10-26: optional caller-selected disk index path and
append operation reuse the same schema/tokenization/scoring. The separate Youmi
cache coordinator reconciles authorized canonical snapshots and crash markers;
it does not restore the upstream database, public scope, or directory management.
Disk support is explicit synthetic-data shadow evaluation only, with no default
production path or default reply activation. See docs/memory-takeover/index-recovery.md.
