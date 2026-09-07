"""
retrieval_eval.py  —  does the knowledge base actually retrieve anything useful?
=====================================================================
    uv run python memory/retrieval_eval.py

An indexed vector store that returns plausible-looking text is easy. One
that returns text a decision should actually be conditioned on is not,
and the difference is invisible unless it's measured. This is that
measurement, run against the REAL indexed corpus in memory/_store — no
synthetic fixtures.

HOW THE EVAL SET IS BUILT (no hand-labelling, no leakage)
    Every indexing call writes chunks tagged with (symbol, date, section,
    parent_id). For each such past run P on symbol S dated D, a query is
    built from P's own downside text (bear case + key risk) and issued
    with `exclude_symbol_date=(S, D)` — so P's own chunks CANNOT be
    returned. A retrieved chunk counts as relevant if it is:
        - the same symbol S from a DIFFERENT date  (that stock's history), or
        - a different stock in the same sector      (comparable situation)
    which is exactly what agents/decision_agent.py asks the store for.

    A run whose symbol has no other dated entry AND whose sector has no
    other stock indexed has no reachable ground truth, so it is SKIPPED
    and reported separately rather than silently scored as a miss — a
    cold corpus should read as "not enough data yet", not as "retrieval
    is broken".

WHAT IS REPORTED
    hit_rate@k   -- share of queries with >=1 relevant chunk in the top k
    precision@k  -- share of returned chunks that are relevant
    mrr          -- mean reciprocal rank of the first relevant chunk
    random_baseline_precision -- precision you'd get picking chunks
                    uniformly at random, so the numbers above can be read
                    as lift over chance rather than in isolation

The retrieval being measured goes through the PRODUCTION path
(KnowledgeBase.retrieve_context), not a private re-implementation, so the
score reflects what the Decision Agent actually gets. Corpus enumeration
below reaches into the collection directly — that part is eval-harness
only, never used at runtime.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory.knowledge_base import KnowledgeBase  # noqa: E402

OUT_PATH = ROOT / "output" / "retrieval_eval.json"


def _corpus(kb: KnowledgeBase) -> list:
    """Every indexed chunk with its metadata. Eval-harness only."""
    try:
        raw = kb._collection.get(include=["metadatas", "documents"])  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        print(f"[retrieval_eval] could not read the collection: {e}")
        return []
    metas = raw.get("metadatas") or []
    docs = raw.get("documents") or []
    return [
        {**(m or {}), "text": d}
        for m, d in zip(metas, docs)
    ]


def _build_queries(corpus: list) -> list:
    """One query per indexed run (parent_id), built from that run's own
    downside text — the kind of text the Decision Agent queries with."""
    by_parent = {}
    for row in corpus:
        pid = row.get("parent_id")
        if not pid:
            continue
        by_parent.setdefault(pid, []).append(row)

    queries = []
    for pid, rows in by_parent.items():
        downside = [
            r["text"] for r in rows
            if r.get("section") in ("bear_case", "key_risk")
        ]
        if not downside:
            downside = [r["text"] for r in rows]
        symbol = rows[0].get("symbol")
        sector = rows[0].get("sector")
        date = rows[0].get("date")
        if not symbol:
            continue
        queries.append({
            "parent_id": pid,
            "symbol": symbol,
            "sector": sector,
            "date": date,
            "query_text": " ".join(downside)[:1000],
        })
    return queries


def _has_ground_truth(query: dict, corpus: list) -> bool:
    """Is anything relevant even reachable, given self-exclusion?"""
    for row in corpus:
        if row.get("symbol") == query["symbol"] and row.get("date") != query["date"]:
            return True
        if (row.get("sector") == query["sector"]
                and row.get("symbol") != query["symbol"]
                and query.get("sector") not in (None, "n/a")):
            return True
    return False


def _is_relevant(hit: dict, query: dict) -> bool:
    if hit.get("symbol") == query["symbol"] and hit.get("date") != query["date"]:
        return True
    if (query.get("sector") not in (None, "n/a")
            and hit.get("symbol") != query["symbol"]):
        # Sector match is only checkable via the corpus, so the caller
        # resolves it — see run_eval, which passes sector-resolved hits.
        return bool(hit.get("_same_sector"))
    return False


def _random_baseline_precision(query: dict, corpus: list) -> float:
    """Share of the corpus (excluding self-excluded chunks) that would
    count as relevant — i.e. precision from picking at random."""
    eligible = [r for r in corpus
                if not (r.get("symbol") == query["symbol"] and r.get("date") == query["date"])]
    if not eligible:
        return 0.0
    relevant = 0
    for r in eligible:
        same_symbol_other_date = (
            r.get("symbol") == query["symbol"] and r.get("date") != query["date"]
        )
        same_sector_other_symbol = (
            query.get("sector") not in (None, "n/a")
            and r.get("sector") == query["sector"]
            and r.get("symbol") != query["symbol"]
        )
        if same_symbol_other_date or same_sector_other_symbol:
            relevant += 1
    return relevant / len(eligible)


def run_eval(k: int = 3, min_score: float = 0.0) -> dict:
    """min_score defaults to 0 here so the eval measures the RANKER, not
    the production threshold — thresholding is reported separately via
    the score distribution."""
    kb = KnowledgeBase()
    if not kb.available:
        return {"available": False,
                "summary": "chromadb unavailable — retrieval is disabled, nothing to evaluate."}

    corpus = _corpus(kb)
    if not corpus:
        return {"available": True, "corpus_chunks": 0,
                "summary": "Knowledge base is empty — run run.py at least once so "
                           "research gets indexed, then re-run this eval."}

    sector_by_symbol = {}
    for row in corpus:
        if row.get("symbol"):
            sector_by_symbol.setdefault(row["symbol"], row.get("sector"))

    queries = _build_queries(corpus)
    evaluable = [q for q in queries if _has_ground_truth(q, corpus)]
    skipped = len(queries) - len(evaluable)

    if not evaluable:
        return {
            "available": True,
            "corpus_chunks": len(corpus),
            "total_queries": len(queries),
            "evaluable_queries": 0,
            "skipped_no_ground_truth": skipped,
            "summary": "Not enough history yet — every indexed stock appears only "
                       "once, so no query has a reachable relevant result. Run the "
                       "pipeline on more days (or more stocks in a shared sector) "
                       "and re-run this eval.",
        }

    hits_at_k, precisions, reciprocal_ranks, baselines, top_scores = [], [], [], [], []
    per_query = []

    for q in evaluable:
        results = kb.retrieve_context(
            query_text=q["query_text"],
            exclude_symbol_date=(q["symbol"], q["date"]),
            n_results=k,
            min_score=min_score,
            max_per_symbol=k,  # don't let the diversity cap distort the ranker score
        )
        for r in results:
            r["_same_sector"] = (
                sector_by_symbol.get(r.get("symbol")) == q.get("sector")
                and q.get("sector") not in (None, "n/a")
            )

        flags = [_is_relevant(r, q) for r in results]
        first_rank = next((i + 1 for i, ok in enumerate(flags) if ok), None)

        hits_at_k.append(1 if first_rank else 0)
        precisions.append(sum(flags) / len(flags) if flags else 0.0)
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        baselines.append(_random_baseline_precision(q, corpus))
        if results:
            top_scores.append(results[0]["score"])

        per_query.append({
            "symbol": q["symbol"], "date": q["date"], "sector": q["sector"],
            "returned": len(results), "relevant": sum(flags),
            "first_relevant_rank": first_rank,
            "top_result": (f"{results[0]['symbol']} {results[0]['date']} "
                            f"({results[0]['section']}, score {results[0]['score']})")
                           if results else None,
        })

    n = len(evaluable)
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else 0.0  # noqa: E731

    return {
        "available": True,
        "k": k,
        "corpus_chunks": len(corpus),
        "total_queries": len(queries),
        "evaluable_queries": n,
        "skipped_no_ground_truth": skipped,
        "hit_rate_at_k": mean(hits_at_k),
        "precision_at_k": mean(precisions),
        "mrr": mean(reciprocal_ranks),
        "random_baseline_precision": mean(baselines),
        "mean_top_score": mean(top_scores),
        "per_query": per_query,
    }


def print_report(report: dict) -> None:
    if not report.get("available"):
        print(report.get("summary", "Retrieval unavailable."))
        return
    if not report.get("evaluable_queries"):
        print(report.get("summary", "Nothing to evaluate."))
        print(f"  corpus chunks: {report.get('corpus_chunks', 0)}")
        return

    print(f"Retrieval eval over the real indexed corpus  (k={report['k']})")
    print(f"  corpus chunks          : {report['corpus_chunks']}")
    print(f"  queries evaluated      : {report['evaluable_queries']} "
          f"({report['skipped_no_ground_truth']} skipped — no reachable ground truth)")
    print()
    print(f"  hit rate@{report['k']}            : {report['hit_rate_at_k']:.1%}")
    print(f"  precision@{report['k']}           : {report['precision_at_k']:.1%}")
    print(f"  MRR                    : {report['mrr']:.3f}")
    print(f"  mean top-1 score       : {report['mean_top_score']:.3f}")
    print()
    baseline = report["random_baseline_precision"]
    lift = (report["precision_at_k"] / baseline) if baseline else None
    print(f"  random baseline precision: {baseline:.1%}")
    if lift:
        print(f"  → retrieval is {lift:.1f}x better than picking at random")
    else:
        print("  → baseline is 0 (no relevant chunks reachable at all)")


if __name__ == "__main__":
    report = run_eval()
    print_report(report)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved -> {OUT_PATH}")
