"""
knowledge_base.py  —  real retrieval-augmented context for the Decision Agent
==============================================================================
A small, curated set of durable India-equity risk heuristics (sector
sensitivities, common red flags), stored in ChromaDB and retrieved by REAL
semantic similarity search against each stock's sector before the Decision
Agent runs — not static text pasted into every prompt regardless of
relevance, and not fabricated by an LLM at call time.

This is genuine retrieval-augmented generation, deliberately scoped small:
the value being demonstrated is the retrieval pattern itself (embed once,
query per-stock, ground the LLM's prompt in retrieved passages, degrade to
nothing rather than hallucinate if retrieval is unavailable), not a large
document corpus.

    retrieve_for_sector("Banking") -> ["...", "..."]   # up to k passages

Falls back to returning [] if ChromaDB isn't available — callers must not
substitute the whole knowledge base as a fallback, since sending
everything regardless of relevance isn't retrieval.
"""

import os

STORE_DIR = os.path.join(os.path.dirname(__file__), "_store")

_DOCS = [
    {
        "id": "sector-it-export",
        "sector": "IT",
        "text": "IT/export-oriented Indian equities are highly sensitive to USD/INR moves "
                "and US client budget cycles; a stronger rupee compresses reported margins "
                "even with unchanged deal volume.",
    },
    {
        "id": "sector-banking-rbi",
        "sector": "Banking",
        "text": "Indian banking/financial stocks react sharply to RBI repo rate decisions "
                "and asset-quality (NPA) disclosures; a rate-cut cycle typically compresses "
                "net interest margins short-term before volume growth offsets it.",
    },
    {
        "id": "sector-energy-crude",
        "sector": "Energy",
        "text": "Energy and oil-refining names are directly exposed to crude oil price "
                "swings and government fuel-pricing policy; a crude spike squeezes refining "
                "margins unless the company is a pure upstream producer.",
    },
    {
        "id": "sector-auto-cyclicality",
        "sector": "Automobile",
        "text": "Auto sector demand in India is strongly seasonal (festive-quarter skew) and "
                "rate-sensitive on the financing side; rural monsoon outcomes materially "
                "swing two-wheeler and tractor volumes.",
    },
    {
        "id": "sector-pharma-regulatory",
        "sector": "Pharmaceuticals",
        "text": "Pharma exporters carry US FDA plant-inspection risk (an adverse observation "
                "can halt a facility's exports for months) alongside regular India-domestic "
                "pricing-control exposure (NPPA).",
    },
    {
        "id": "sector-metals-commodity",
        "sector": "Metals",
        "text": "Metals/mining names are commodity-cycle plays tied to global demand "
                "(especially China) and INR-denominated import/export cost swings; earnings "
                "are lumpy and margin-, not volume-, driven.",
    },
    {
        "id": "sector-realty-rates",
        "sector": "Realty",
        "text": "Real-estate developer stocks are highly leveraged to home-loan interest "
                "rates and state-level regulatory (RERA) approval timelines; inventory "
                "overhang is a slower-moving but persistent risk.",
    },
    {
        "id": "sector-fmcg-defensive",
        "sector": "FMCG",
        "text": "FMCG names are typically defensive with lower volatility, but rural demand "
                "weakness and input-cost (palm oil, crude derivatives) inflation are the two "
                "swing factors on margin.",
    },
    {
        "id": "risk-earnings-surprise",
        "sector": "general",
        "text": "A single-quarter earnings beat or miss driven by a one-off item (asset "
                "sale, tax credit, impairment) should be weighted less than a trend visible "
                "over 2-3 consecutive quarters.",
    },
    {
        "id": "risk-liquidity-exit",
        "sector": "general",
        "text": "A stock's average daily traded volume relative to a target position size "
                "determines real exit risk — a thesis can be correct and still be "
                "unrealisable if the position can't be unwound without moving the price.",
    },
    {
        "id": "risk-index-rebalance",
        "sector": "general",
        "text": "Stocks near Nifty index inclusion/exclusion thresholds can see mechanical "
                "passive-fund flows unrelated to fundamentals around each index rebalance date.",
    },
    {
        "id": "risk-promoter-pledge",
        "sector": "general",
        "text": "A high or rising promoter share-pledge percentage is a red flag independent "
                "of headline fundamentals — a forced margin-call sale can hit the stock "
                "regardless of business quality.",
    },
]

_collection = None
_init_failed = False


def _get_collection():
    global _collection, _init_failed
    if _collection is not None or _init_failed:
        return _collection
    try:
        import chromadb
        client = chromadb.PersistentClient(path=STORE_DIR)
        collection = client.get_or_create_collection("knowledge_base")
        if collection.count() == 0:
            collection.add(
                documents=[d["text"] for d in _DOCS],
                metadatas=[{"sector": d["sector"]} for d in _DOCS],
                ids=[d["id"] for d in _DOCS],
            )
        _collection = collection
    except Exception:
        _init_failed = True
        _collection = None
    return _collection


def retrieve_for_sector(sector: str, k: int = 2) -> list:
    """
    Real semantic retrieval via ChromaDB's vector similarity search — not a
    hardcoded {sector: text} lookup table. Returns up to k relevant
    passages for this stock's sector, or [] if retrieval isn't available or
    no sector was given.
    """
    if not sector:
        return []
    collection = _get_collection()
    if collection is None:
        return []
    try:
        results = collection.query(
            query_texts=[f"key risk factors for the {sector} sector in Indian equities"],
            n_results=k,
        )
        return results.get("documents", [[]])[0]
    except Exception:
        return []
