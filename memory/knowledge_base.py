"""
knowledge_base.py  —  chunked, controllable knowledge retrieval
=====================================================================
memory/memory_layer.py stores STRUCTURED deltas (price, scores) per
symbol, looked up by exact symbol match — useful for "what changed since
last time," but it isn't retrieval: it can only answer "what did we say
about THIS exact symbol," never "what have we seen before that's actually
RELEVANT to this situation."

This module is that missing step, and it does three things a plain
"embed the whole note" store doesn't:

CHUNKING STRATEGY
    A run's research on one stock is not one homogeneous blob — it is a
    news event summary, a bull case, a bear case, and a key risk, which
    are about different things and should match different queries. Each
    is indexed as its own SECTION chunk, tagged with `section` metadata,
    so a query about downside risk can match a bear case directly instead
    of being diluted by the bull case sitting in the same embedding. Any
    section longer than CHUNK_CHARS is further split into overlapping
    windows at sentence boundaries (CHUNK_OVERLAP characters of overlap),
    so a point made across a window edge is still retrievable from both
    sides. All chunks from one indexing call share a `parent_id`, so a
    retrieved chunk can be traced back to the run it came from.

RETRIEVAL CONTROLS
    retrieve_context() exposes real, tunable controls rather than a bare
    top-k: a similarity floor (`min_score`) so weak matches are dropped
    instead of padding the results, sector and recency (`date_after`)
    filters, per-symbol diversity capping (`max_per_symbol`) so one
    stock's chunks can't monopolise every slot, section filtering, and
    self-exclusion. Every retrieval records what it was asked for and
    what it returned to the always-on metrics stream.

EVALUATION
    memory/retrieval_eval.py measures whether any of this actually works
    — hit rate, MRR and precision@k over held-out queries built from the
    real indexed corpus — and writes output/retrieval_eval.json.

Falls back to doing nothing (never raises) if chromadb isn't installed —
a missing knowledge base should never block a run.
"""

import datetime as dt
import os
import re
import uuid

import observability

STORE_DIR = os.path.join(os.path.dirname(__file__), "_store")
COLLECTION_NAME = "research_notes"

# Chunking parameters. Research sections here are short (a few sentences),
# so the window is sized to keep a whole typical section intact and only
# split genuinely long ones — over-chunking short text destroys the
# context that makes it retrievable at all.
CHUNK_CHARS = 700
CHUNK_OVERLAP = 120

# Default retrieval controls.
DEFAULT_TOP_K = 3
DEFAULT_MIN_SCORE = 0.25
DEFAULT_MAX_PER_SYMBOL = 2

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS,
                overlap: int = CHUNK_OVERLAP) -> list:
    """Splits `text` into overlapping windows that prefer sentence
    boundaries. Short text comes back as a single chunk — the common case
    here, and deliberately not split further."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    sentences = _SENTENCE_END_RE.split(text)
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > chunk_chars:
            chunks.append(current.strip())
            # Carry the tail of the finished chunk into the next one so a
            # point spanning the boundary stays retrievable from both.
            carry = current[-overlap:] if overlap else ""
            current = (carry + " " + sentence).strip()
        else:
            current = f"{current} {sentence}".strip()
    if current.strip():
        chunks.append(current.strip())

    # A single sentence longer than the window still has to be cut.
    out = []
    for chunk in chunks:
        while len(chunk) > chunk_chars:
            out.append(chunk[:chunk_chars])
            chunk = chunk[chunk_chars - overlap:]
        if chunk:
            out.append(chunk)
    return out


class KnowledgeBase:
    def __init__(self):
        self.available = False
        self._space = "l2"
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=STORE_DIR)
            # Cosine gives a bounded, interpretable distance (0..2) that
            # maps cleanly onto a 0..1 similarity score. An existing
            # collection keeps whatever space it was created with — the
            # metadata read below detects which, so scoring stays correct
            # either way rather than silently mis-scaling.
            self._collection = self._client.get_or_create_collection(
                COLLECTION_NAME, metadata={"hnsw:space": "cosine"},
            )
            meta = self._collection.metadata or {}
            self._space = meta.get("hnsw:space", "l2")
            self.available = True
        except Exception as e:  # noqa: BLE001
            print(f"[knowledge_base] unavailable, retrieval disabled this run ({e})")

    # ------------------------------------------------------------------
    def _score(self, distance: float) -> float:
        """Distance -> a 0..1 relevance score, higher = more similar.
        Cosine distance is bounded so it maps directly; L2 is unbounded so
        it gets a monotone squash instead. Both are monotone in distance,
        which is what min_score thresholding actually depends on."""
        if distance is None:
            return 0.0
        if self._space == "cosine":
            return max(0.0, min(1.0, 1.0 - float(distance)))
        return 1.0 / (1.0 + float(distance))

    # ------------------------------------------------------------------
    def index_run(self, symbol: str, sector: str, event_summary: str = "",
                   bull_case: str = "", bear_case: str = "", key_risk: str = "",
                   run_date: dt.date = None) -> int:
        """Index today's real research for `symbol` as SECTION-tagged,
        overlap-chunked documents so future runs can retrieve it. Returns
        the number of chunks written. Best-effort — never raises."""
        if not self.available:
            return 0
        run_date = run_date or dt.date.today()
        parent_id = uuid.uuid4().hex[:12]

        sections = {
            "event_summary": event_summary,
            "bull_case": bull_case,
            "bear_case": bear_case,
            "key_risk": key_risk,
        }

        documents, metadatas, ids = [], [], []
        for section, raw in sections.items():
            for i, chunk in enumerate(chunk_text(raw)):
                # The section label rides along in the embedded text as
                # well as in metadata — it is real semantic context ("this
                # is a bear case"), not just a filter key.
                documents.append(f"[{symbol} · {section.replace('_', ' ')}] {chunk}")
                metadatas.append({
                    "symbol": symbol,
                    "sector": sector or "n/a",
                    "date": run_date.isoformat(),
                    # Chroma's range operators ($gte/$lt) only accept numbers,
                    # so recency filtering needs a numeric form of the date
                    # alongside the human-readable ISO one. Filtering on the
                    # ISO string silently returns nothing instead of erroring
                    # loudly, which is exactly the kind of quiet wrong answer
                    # a retrieval layer must not have.
                    "date_num": int(run_date.strftime("%Y%m%d")),
                    "section": section,
                    "chunk_index": i,
                    "parent_id": parent_id,
                })
                ids.append(f"{parent_id}-{section}-{i}")

        if not documents:
            return 0

        try:
            self._collection.add(documents=documents, metadatas=metadatas, ids=ids)
        except Exception as e:  # noqa: BLE001
            print(f"[knowledge_base] could not index {symbol}: {e}")
            observability.record_failure("knowledge_base_index", str(e), symbol=symbol)
            return 0

        observability.record_metric(
            "kb_index", symbol=symbol, sector=sector or "n/a", chunks=len(documents),
        )
        return len(documents)

    # ------------------------------------------------------------------
    def retrieve_context(self, query_text: str, sector: str = None,
                          exclude_symbol_date: tuple = None,
                          n_results: int = DEFAULT_TOP_K,
                          min_score: float = DEFAULT_MIN_SCORE,
                          max_per_symbol: int = DEFAULT_MAX_PER_SYMBOL,
                          sections: list = None,
                          date_after: str = None) -> list:
        """Semantic search over past indexed research, with real controls.

        n_results      -- how many chunks to return AFTER filtering
        min_score      -- drop anything below this relevance (0..1). A weak
                          match is worse than no match: it spends prompt
                          budget and invites the model to anchor on
                          something irrelevant.
        max_per_symbol -- diversity cap, so one stock's chunks can't fill
                          every slot when the query is about that stock
        sections       -- e.g. ["bear_case", "key_risk"] to retrieve only
                          downside context
        date_after     -- ISO date string; only notes indexed on or after it
        exclude_symbol_date -- (symbol, date_iso) to skip, so a stock can't
                          retrieve its own note from today's in-flight run

        Returns [{"symbol", "date", "section", "score", "text"}], best
        first. Empty list (never raises) if unavailable or nothing clears
        the filters — a cold-started knowledge base is a normal state."""
        if not self.available:
            return []

        where = {}
        if sector:
            where["sector"] = sector
        if sections:
            where["section"] = {"$in": list(sections)}
        if date_after:
            try:
                where["date_num"] = {"$gte": int(str(date_after).replace("-", ""))}
            except ValueError:
                print(f"[knowledge_base] ignoring unparseable date_after={date_after!r}")
        # Chroma requires an explicit $and once there is more than one key.
        if len(where) > 1:
            where = {"$and": [{k: v} for k, v in where.items()]}
        where = where or None

        # Over-fetch, because min_score / max_per_symbol / self-exclusion
        # all filter AFTER the vector search — asking for exactly n_results
        # here would routinely return fewer than asked once filters apply.
        fetch_k = max(n_results * 4, 12)

        try:
            res = self._collection.query(
                query_texts=[query_text], n_results=fetch_k, where=where,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[knowledge_base] retrieval failed: {e}")
            observability.record_failure("knowledge_base_query", str(e))
            return []

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0] or [None] * len(docs)

        out, per_symbol = [], {}
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            score = self._score(dist)
            if score < min_score:
                continue
            symbol = meta.get("symbol")
            if exclude_symbol_date and (symbol, meta.get("date")) == exclude_symbol_date:
                continue
            if per_symbol.get(symbol, 0) >= max_per_symbol:
                continue
            per_symbol[symbol] = per_symbol.get(symbol, 0) + 1
            out.append({
                "symbol": symbol,
                "date": meta.get("date"),
                "section": meta.get("section"),
                "score": round(score, 3),
                "text": doc,
            })
            if len(out) >= n_results:
                break

        observability.record_metric(
            "kb_retrieval", query_chars=len(query_text or ""), sector=sector or "any",
            candidates=len(docs), returned=len(out),
            top_score=out[0]["score"] if out else None,
            min_score=min_score, top_k=n_results,
        )
        return out

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """Corpus size and shape — shown in the dashboard's Ops tab so the
        retrieval layer's real state is visible, not assumed."""
        if not self.available:
            return {"available": False, "chunks": 0}
        try:
            count = self._collection.count()
            sample = self._collection.get(limit=min(count, 2000), include=["metadatas"])
            metas = sample.get("metadatas") or []
        except Exception:  # noqa: BLE001
            return {"available": True, "chunks": 0}

        symbols = {m.get("symbol") for m in metas if m.get("symbol")}
        sectors = {m.get("sector") for m in metas if m.get("sector")}
        dates = sorted({m.get("date") for m in metas if m.get("date")})
        by_section = {}
        for m in metas:
            key = m.get("section", "unknown")
            by_section[key] = by_section.get(key, 0) + 1

        return {
            "available": True,
            "chunks": count,
            "distinct_symbols": len(symbols),
            "distinct_sectors": len(sectors),
            "by_section": by_section,
            "earliest": dates[0] if dates else None,
            "latest": dates[-1] if dates else None,
            "distance_space": self._space,
        }
