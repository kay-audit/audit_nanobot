from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

_TOKEN = re.compile(r"[a-zа-яё0-9_]+", re.IGNORECASE)


def tokenize(text: Any) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN.finditer(str(text or ""))]


@dataclass(frozen=True)
class HybridDocument:
    id: str
    text: str
    group: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    id: str
    score: float
    metadata: dict[str, Any]
    dense_score: float | None = None
    bm25_score: float | None = None
    fused_score: float | None = None


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[SearchHit]
    candidates_total: int
    dropped_below_floor: int
    dropped_by_group_collapse: int
    low_confidence: bool


def prepare_from_frame(rows: Iterable[Mapping[str, Any]], *, id_field: str = "id", text_fields: Sequence[str], group_field: str | None = None, metadata_fields: Sequence[str] | None = None) -> list[HybridDocument]:
    """Create deterministic index documents without retaining SQL payloads."""
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict(orient="records")  # type: ignore[assignment]
    docs: list[HybridDocument] = []
    seen: set[str] = set()
    for row in rows:
        doc_id = str(row.get(id_field, ""))
        if not doc_id or doc_id in seen:
            continue
        text = "\n".join(str(row.get(field) or "") for field in text_fields).strip()
        if not text:
            continue
        seen.add(doc_id)
        metadata = dict(row) if metadata_fields is None else {key: row.get(key) for key in metadata_fields}
        docs.append(HybridDocument(doc_id, text, str(row.get(group_field) or "") if group_field else "", metadata))
    return docs


class HybridIndex:
    """Persistent BM25/FAISS searcher with a small in-memory test fallback."""

    def __init__(self, documents: Sequence[HybridDocument], *, embedder: Callable[[list[str]], Any] | None = None, reranker: Callable[[str, list[str]], Sequence[float]] | None = None, dense_vectors: Mapping[str, Sequence[float]] | None = None, faiss_index: Any = None, bm25_index: Any = None, build_id: str = "runtime", faiss_k: int = 750, bm25_k: int = 200, rerank_k: int = 100, rrf_k: int = 60, rrf_alpha: float = 0.3) -> None:
        self.documents = list(documents)
        self.build_id = build_id
        self._by_id = {doc.id: doc for doc in self.documents}
        self._position_by_id = {doc.id: position for position, doc in enumerate(self.documents)}
        self.embedder, self.reranker = embedder, reranker
        self.dense_vectors = {str(key): list(value) for key, value in (dense_vectors or {}).items()}
        self.faiss_index, self.bm25_index = faiss_index, bm25_index
        self.faiss_k, self.bm25_k, self.rerank_k = int(faiss_k), int(bm25_k), int(rerank_k)
        self.rrf_k, self.rrf_alpha = int(rrf_k), float(rrf_alpha)
        # Persisted bm25s already contains tokenization/statistics. Building these
        # arrays again would defeat mmap and is reserved for small in-memory uses.
        self._tokens = [] if bm25_index is not None else [tokenize(doc.text) for doc in self.documents]
        self._df = Counter(token for tokens in self._tokens for token in set(tokens))
        self._avgdl = sum(map(len, self._tokens)) / max(1, len(self._tokens))

    def _dense(self, query: str, candidates: set[str] | None = None) -> list[tuple[str, float]]:
        if not self.embedder:
            return []
        if self.faiss_index is not None:
            import numpy as np
            query_vector = np.asarray(self.embedder([query]), dtype="float32")
            if candidates is not None:
                rows = []
                query_row = query_vector[0]
                for doc_id in candidates:
                    position = self._position_by_id.get(doc_id)
                    if position is None:
                        continue
                    vector = np.asarray(self.faiss_index.reconstruct(position), dtype="float32")
                    rows.append((doc_id, float(np.dot(query_row, vector))))
                return sorted(rows, key=lambda item: (-item[1], item[0]))
            k = min(max(0, self.faiss_k), len(self.documents))
            if not k:
                return []
            distances, positions = self.faiss_index.search(query_vector, k)
            rows = []
            for position, score in zip(positions[0], distances[0]):
                pos = int(position)
                if 0 <= pos < len(self.documents):
                    doc_id = self.documents[pos].id
                    if candidates is None or doc_id in candidates:
                        rows.append((doc_id, float(score)))
            return rows
        selected = [doc for doc in self.documents if candidates is None or doc.id in candidates]
        if not selected:
            return []
        if self.dense_vectors and all(doc.id in self.dense_vectors for doc in selected):
            query_vector = self.embedder([query])[0]
            document_vectors = [self.dense_vectors[doc.id] for doc in selected]
        else:
            vectors = self.embedder([query] + [doc.text for doc in selected])
            query_vector, document_vectors = vectors[0], list(vectors[1:])
        def cosine(left, right):
            dot = sum(float(a) * float(b) for a, b in zip(left, right))
            left_norm = math.sqrt(sum(float(a) ** 2 for a in left)); right_norm = math.sqrt(sum(float(b) ** 2 for b in right))
            return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
        return sorted(((doc.id, cosine(query_vector, document_vectors[pos])) for pos, doc in enumerate(selected)), key=lambda item: (-item[1], item[0]))

    def _bm25(self, query: str, candidates: set[str] | None = None) -> list[tuple[str, float]]:
        query_tokens = tokenize(query)
        if candidates is not None and self.bm25_index is not None:
            subset = [self._by_id[doc_id] for doc_id in candidates if doc_id in self._by_id]
            return HybridIndex(subset)._bm25(query)
        if self.bm25_index is not None:
            valid = [token for token in query_tokens if token in self.bm25_index.vocab_dict]
            if not valid:
                return []
            result = self.bm25_index.retrieve([valid], k=min(max(0, self.bm25_k), len(self.documents)), sorted=True, show_progress=False)
            rows = []
            for position, score in zip(result.documents[0], result.scores[0]):
                pos = int(position)
                if 0 <= pos < len(self.documents):
                    doc_id = self.documents[pos].id
                    if candidates is None or doc_id in candidates:
                        rows.append((doc_id, float(score)))
            return rows
        n_docs = max(1, len(self.documents)); scored = []
        for doc, tokens in zip(self.documents, self._tokens):
            if candidates is not None and doc.id not in candidates:
                continue
            frequencies = Counter(tokens); score = 0.0
            for term in query_tokens:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                df = self._df.get(term, 0); inverse = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * len(tokens) / max(self._avgdl, 1))
                score += inverse * frequency * 2.5 / denominator
            if score > 0:
                scored.append((doc.id, score))
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    def _preliminary(self, query: str, candidates: set[str] | None) -> list[dict[str, Any]]:
        lexical, dense = self._bm25(query, candidates), self._dense(query, candidates)
        bm_rank = {doc_id: pos for pos, (doc_id, _) in enumerate(lexical, 1)}; dense_rank = {doc_id: pos for pos, (doc_id, _) in enumerate(dense, 1)}
        bm_score, dense_score = dict(lexical), dict(dense); missing = 999; rows = []
        for doc_id in set(bm_rank) | set(dense_rank):
            fused = self.rrf_alpha / (self.rrf_k + bm_rank.get(doc_id, missing)) + (1 - self.rrf_alpha) / (self.rrf_k + dense_rank.get(doc_id, missing))
            rows.append({"id": doc_id, "fused": fused, "bm25": bm_score.get(doc_id), "dense": dense_score.get(doc_id)})
        return sorted(rows, key=lambda row: (-row["fused"], row["id"]))

    @staticmethod
    def _channel_confidence(row: Mapping[str, Any]) -> float:
        lexical = float(row.get("bm25") or 0.0); dense = row.get("dense")
        lexical_confidence = lexical / (1.0 + lexical) if lexical > 0 else 0.0
        dense_confidence = max(0.0, min(1.0, (float(dense) + 1.0) / 2.0)) if dense is not None else 0.0
        return max(lexical_confidence, dense_confidence)

    def search_with_diagnostics(self, query: str, *, top_k: int = 5, ids: Iterable[Any] | None = None, collapse_groups: bool = True, score_floor: float = 0.4, allow_low_confidence_fallback: bool = True) -> SearchOutcome:
        candidates = {str(value) for value in ids} if ids is not None else None
        preliminary = self._preliminary(query, candidates); groups: set[str] = set(); unique = []; dropped_by_group = 0
        for row in preliminary:
            document = self._by_id[row["id"]]
            if collapse_groups and document.group and document.group in groups:
                dropped_by_group += 1; continue
            if document.group: groups.add(document.group)
            unique.append(row)
        pool = unique[:max(max(1, int(top_k)), self.rerank_k)]
        if self.reranker and pool:
            for row, score in zip(pool, self.reranker(query, [self._by_id[row["id"]].text for row in pool])): row["score"] = float(score)
            pool.sort(key=lambda row: (-row["score"], row["id"]))
        else:
            for row in pool: row["score"] = self._channel_confidence(row)
        accepted = [row for row in pool if float(row["score"]) >= float(score_floor)]
        low_confidence = not accepted and bool(pool); selected = accepted[:max(1, int(top_k))]
        if low_confidence and allow_low_confidence_fallback: selected = pool[:1]
        hits = [SearchHit(row["id"], float(row["score"]), dict(self._by_id[row["id"]].metadata), row.get("dense"), row.get("bm25"), row.get("fused")) for row in selected]
        return SearchOutcome(hits, len(preliminary), len(pool) - len(accepted), dropped_by_group, low_confidence)

    def search(self, query: str, **kwargs: Any) -> list[SearchHit]:
        return self.search_with_diagnostics(query, **kwargs).hits

    def rank_ids_with_diagnostics(self, query: str, ids: Iterable[Any], *, top_k: int | None = None, score_floor: float = 0.0) -> SearchOutcome:
        values = list(dict.fromkeys(str(value) for value in ids))
        if not values: return SearchOutcome([], 0, 0, 0, False)
        known = [value for value in values if value in self._by_id]
        if not known: raise ValueError(f"None of the supplied IDs belong to corpus build {self.build_id}")
        if self.reranker:
            rows = sorted(zip(known, self.reranker(query, [self._by_id[doc_id].text for doc_id in known])), key=lambda item: (-float(item[1]), item[0]))
            dropped_floor = sum(float(score) < score_floor for _, score in rows)
            groups: set[str] = set(); hits = []; dropped_groups = 0
            for doc_id, score in rows:
                document = self._by_id[doc_id]
                if float(score) < score_floor: continue
                if document.group and document.group in groups:
                    dropped_groups += 1; continue
                if document.group: groups.add(document.group)
                hits.append(SearchHit(doc_id, float(score), dict(document.metadata)))
            selected = hits[:(top_k or len(hits))]
            return SearchOutcome(selected, len(known), dropped_floor, dropped_groups, not selected and bool(rows))
        subset = HybridIndex([self._by_id[doc_id] for doc_id in known], build_id=self.build_id)
        return subset.search_with_diagnostics(query, top_k=top_k or len(known), score_floor=score_floor, allow_low_confidence_fallback=False)

    def rank_ids(self, query: str, ids: Iterable[Any], *, top_k: int | None = None, score_floor: float = 0.0) -> list[SearchHit]:
        return self.rank_ids_with_diagnostics(query, ids, top_k=top_k, score_floor=score_floor).hits
