# SPDX-License-Identifier: GPL-3.0-only
# Adapted from astrbot_plugin_angel_memory by kawayiYokami and contributors.
# Modified for Youmi, 2026-10-25. See YOUMI_MEMORY_NOTICE.md and YOUMI_MEMORY_LICENSE.
"""Angel Memory CJK/BM25/RRF core for RAM or explicit shadow disk evaluation."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import tantivy


def normalize_search_text(text: str) -> str:
    """统一全角→半角、去引号、小写"""
    if not isinstance(text, str):
        return ""
    result = text.strip().lower()
    # 全角→半角
    result = re.sub(
        r"[\uff01-\uff5e]",
        lambda ch: chr(ord(ch.group(0)) - 0xFEE0),
        result,
    )
    result = result.replace("\u3000", " ")
    # 统一引号
    result = re.sub(r'["\u201c\u201d\u2018\u2019]', '"', result)
    result = re.sub(r'^"+|"+$', "", result)
    return result.strip()


def iter_cjk_fragments(
    text: str,
    include_unigram: bool = True,
    max_ngram: int = 2,
) -> List[str]:
    """提取 CJK bigram + 英文 token"""
    normalized = normalize_search_text(text)
    if not normalized:
        return []

    tokens: List[str] = []
    seen: set = set()
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", normalized)
    for part in parts:
        if re.search(r"[\u4e00-\u9fff]", part):
            min_ngram = 1 if include_unigram else 2
            upper = max(min_ngram, int(max_ngram or min_ngram))
            for ngram_size in range(min_ngram, upper + 1):
                if len(part) < ngram_size:
                    continue
                for i in range(len(part) - ngram_size + 1):
                    fragment = part[i : i + ngram_size]
                    if fragment not in seen:
                        seen.add(fragment)
                        tokens.append(fragment)
        else:
            if part not in seen:
                seen.add(part)
                tokens.append(part)
    return tokens


def select_required_cjk_ngrams(query: str) -> List[str]:
    """选择严格匹配所需的 ngram（2字为主，短词保留原样）"""
    normalized = normalize_search_text(query)
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", normalized)
    terms: List[str] = []
    seen: set = set()
    for part in parts:
        if not re.search(r"[\u4e00-\u9fff]", part):
            if part not in seen:
                seen.add(part)
                terms.append(part)
            continue
        if len(part) <= 2:
            if part not in seen:
                seen.add(part)
                terms.append(part)
            continue
        for index in range(len(part) - 1):
            bigram = part[index : index + 2]
            if bigram not in seen:
                seen.add(bigram)
                terms.append(bigram)
    return terms


def tokenize_for_index(text: str) -> str:
    """为索引生成分词文本（CJK bigram + 英文 token，空格拼接）"""
    return " ".join(iter_cjk_fragments(text, include_unigram=True, max_ngram=2))


def _escape_query_term(term: str) -> str:
    """转义 Tantivy query parser 保留字符"""
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    escaped = re.sub(r"([+\-!(){}\[\]^~*?:/])", r"\\\1", escaped)
    return escaped


def _build_all_terms_query(terms: List[str]) -> str:
    """所有词都必须命中"""
    if not terms:
        return ""
    return " ".join(f"+{_escape_query_term(t)}" for t in terms)


def _build_any_terms_query(terms: List[str]) -> str:
    """任一词命中即可"""
    if not terms:
        return ""
    return " ".join(_escape_query_term(t) for t in terms)



class YoumiBM25Search:
    """One owner snapshot; disk callers serialize all access through the cache."""

    def __init__(self, path=None):
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("item_id", stored=True, tokenizer_name="raw")
        builder.add_text_field("tags_text", stored=False)
        builder.add_text_field("judgment_text", stored=False)
        self._schema = builder.build()
        self._index = tantivy.Index(self._schema, path=str(path)) if path is not None else tantivy.Index(self._schema)

    @staticmethod
    def _document(row):
        return tantivy.Document(item_id=[row['id']],
            tags_text=[tokenize_for_index(' '.join(row['tags']))],
            judgment_text=[tokenize_for_index(row['judgment'])])

    def append(self, rows):
        writer = self._index.writer(heap_size=15_000_000, num_threads=1)
        for row in rows:
            writer.add_document(self._document(row))
        writer.commit()
        self._index.reload()

    def rebuild(self, rows):
        writer = self._index.writer(heap_size=15_000_000, num_threads=1)
        writer.delete_all_documents()
        for row in rows:
            writer.add_document(self._document(row))
        writer.commit()
        # A committed writer alone did not make a new reader immediately visible
        # in the pinned upstream experiment.
        self._index.reload()

    def _staged_search(self, query, limit):
        required = select_required_cjk_ngrams(query)
        loose = iter_cjk_fragments(query, include_unigram=True, max_ngram=2)
        searcher = self._index.searcher()
        candidates, seen = [], set()
        for terms, build in [(required, _build_all_terms_query), (loose, _build_any_terms_query)]:
            if not terms:
                continue
            parsed = self._index.parse_query(build(terms), ["tags_text", "judgment_text"])
            for score, address in searcher.search(parsed, limit * 3).hits:
                item_id = searcher.doc(address).to_dict()["item_id"][0]
                if item_id not in seen:
                    candidates.append({"item_id": item_id, "bm25_score": float(score)})
                    seen.add(item_id)
            if len(candidates) >= limit:
                break
        # Unlike upstream broad exception handling, parser/index failures propagate
        # to the shadow caller as unavailable, rather than masquerading as no match.
        return self._score_normalize(candidates[:limit * 3])

    def search(self, query, vector_scores, limit=4):
        rows = self._staged_search(query, 24)
        if vector_scores:
            # Fusion ranks evidence; it cannot qualify a lexical-only hit.
            rows = [row for row in rows if row['item_id'] in vector_scores]
            return self._rrf_fuse(rows, vector_scores, threshold=0.5, limit=limit)
        return [dict(id=row["item_id"], bm25_score=row["bm25_score"],
                     bm25_normalized_score=row["bm25_normalized_score"],
                     final_score=row["bm25_normalized_score"], score_kind="normalized")
                for row in rows[:limit]]

    @staticmethod
    def _score_normalize(rows: List[Dict]) -> List[Dict]:
        if not rows:
            return []
        max_score = 0.0
        for row in rows:
            score = float(row.get("bm25_score", 0.0))
            if score > max_score:
                max_score = score
        normalized_rows: List[Dict] = []
        for row in rows:
            score = float(row.get("bm25_score", 0.0))
            normalized = (score / max_score) if max_score > 0.0 else 0.0
            item = dict(row)
            item["bm25_normalized_score"] = normalized
            normalized_rows.append(item)
        return normalized_rows

    @staticmethod
    def _rrf_fuse(
        bm25_rows: List[Dict],
        vector_scores: Optional[Dict[str, float]],
        threshold: float,
        limit: int,
        k: int = 60,
        min_final_score: Optional[float] = None,
    ) -> List[Dict]:
        """Reciprocal Rank Fusion (RRF) 融合 BM25 和向量分数"""
        if not bm25_rows and not vector_scores:
            return []

        # BM25 排名
        bm25_sorted = sorted(bm25_rows, key=lambda x: float(x.get("bm25_score", 0)), reverse=True)
        bm25_rank: Dict[str, int] = {}
        for rank, row in enumerate(bm25_sorted):
            item_id = str(row.get("item_id") or "").strip()
            if item_id:
                bm25_rank[item_id] = rank + 1

        # 向量排名
        vector_rank: Dict[str, int] = {}
        if vector_scores:
            vec_sorted = sorted(vector_scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (item_id, _) in enumerate(vec_sorted):
                vector_rank[item_id] = rank + 1

        # RRF 融合
        all_ids = set(bm25_rank.keys())
        if vector_scores:
            all_ids.update(vector_scores.keys())

        fused: List[Dict] = []
        for item_id in all_ids:
            bm25_r = bm25_rank.get(item_id, len(bm25_sorted) + 1)
            vec_r = vector_rank.get(item_id, len(vector_rank) + 100) if vector_scores else k + 1
            rrf_score = 1.0 / (k + bm25_r) + 1.0 / (k + vec_r)

            bm25_row = next((r for r in bm25_rows if str(r.get("item_id", "")).strip() == item_id), None)
            bm25_score = float(bm25_row.get("bm25_score", 0.0)) if bm25_row else 0.0
            bm25_norm = float(bm25_row.get("bm25_normalized_score", 0.0)) if bm25_row else 0.0
            vector_score = float(vector_scores.get(item_id, 0.0)) if vector_scores else 0.0

            fused.append({
                "id": item_id,
                "bm25_score": bm25_score,
                "bm25_normalized_score": bm25_norm,
                "vector_score": vector_score,
                "final_score": rrf_score,
                "score_kind": "rrf",
            })

        fused.sort(key=lambda x: (x["final_score"], x["id"]), reverse=True)
        if min_final_score is None and threshold > 0.0 and fused:
            top_score = float(fused[0].get("final_score", 0.0) or 0.0)
            if top_score > 0.0:
                keep_ratio = max(0.0, min(1.0, float(threshold)))
                keep_threshold = top_score * keep_ratio
                fused = [
                    item for item in fused
                    if float(item.get("final_score", 0.0) or 0.0) >= keep_threshold
                ]
        elif min_final_score is not None:
            absolute_threshold = max(0.0, float(min_final_score))
            fused = [
                item for item in fused
                if float(item.get("final_score", 0.0) or 0.0) >= absolute_threshold
            ]
        return fused[:max(0, int(limit))]
