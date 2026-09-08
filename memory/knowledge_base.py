"""
knowledge_base.py  —  real retrieval-augmented context for the Decision Agent
==============================================================================
A curated set of India-equity risk source documents, chunked and stored in
ChromaDB, retrieved for each stock via a real three-part RAG pattern:

  1. CHUNKED SOURCE MATERIAL — each sector's source document is a real
     multi-sentence paragraph, not a one-line fact. It's split into
     overlapping sentence-window chunks at indexing time (2 sentences per
     chunk, 1-sentence stride), so a query can retrieve the SPECIFIC
     relevant chunk instead of the whole paragraph regardless of fit.

  2. METADATA FILTERING — every chunk is tagged with its sector at index
     time. Retrieval queries the sector-tagged subset first (an exact
     `where` filter, not semantic guessing) for sector-specific risk,
     then separately queries the "general" subset for cross-sector risk
     factors (liquidity, index rebalancing, promoter pledges) that apply
     regardless of sector — two filtered pools, not one undifferentiated
     search across everything.

  3. QUERY REWRITING — the query text sent to the vector search is built
     from the stock's own real context (sector + the real event type the
     News/Event Reader found + any real risk flags already raised), not a
     single fixed template repeated identically for every stock in a
     sector. Two IT stocks with different real event types retrieve
     different chunks.

    retrieve_for_stock(sector="Banking", event_type="regulatory",
                        risk_flags=["high volatility"]) -> ["...", "..."]

Falls back to returning [] if ChromaDB isn't available — callers must not
substitute the whole knowledge base as a fallback, since sending
everything regardless of relevance isn't retrieval.
"""

import os
import re

STORE_DIR = os.path.join(os.path.dirname(__file__), "_store")

# Real, multi-sentence source paragraphs — the corpus chunking operates on.
_SOURCE_DOCS = [
    {
        "id": "sector-it",
        "sector": "IT",
        "text": (
            "IT/export-oriented Indian equities are highly sensitive to USD/INR moves "
            "and US client budget cycles; a stronger rupee compresses reported margins "
            "even with unchanged deal volume. Discretionary tech spending from BFSI and "
            "retail clients in the US and Europe is typically the first budget line cut "
            "in a slowdown, so revenue growth for large IT services firms often lags the "
            "broader economic cycle by two to three quarters. Visa and immigration policy "
            "changes in the US (H-1B caps, fee increases) directly raise onsite delivery "
            "costs for the offshore-heavy delivery model most Indian IT firms rely on. "
            "Attrition and wage inflation in the domestic talent market compress margins "
            "independently of revenue trends, and a sudden spike in attrition is often an "
            "early signal of internal execution problems before it shows up in earnings."
        ),
    },
    {
        "id": "sector-banking",
        "sector": "Banking",
        "text": (
            "Indian banking and financial stocks react sharply to RBI repo rate decisions "
            "and asset-quality (NPA) disclosures; a rate-cut cycle typically compresses net "
            "interest margins short-term before loan volume growth offsets it. Deposit "
            "mobilisation cost is a live constraint — when deposit growth lags credit "
            "growth, banks bid up deposit rates, which squeezes margins from the funding "
            "side even if lending rates hold. Unsecured retail lending (personal loans, "
            "credit cards) carries regulatory attention risk: the RBI has repeatedly raised "
            "risk weights on this segment when it judged growth to be too fast, directly "
            "hitting capital ratios and slowing disbursal. Foreign institutional ownership "
            "in large private banks is often high, making these names disproportionately "
            "exposed to global risk-off flows and Fed policy surprises, independent of "
            "domestic fundamentals."
        ),
    },
    {
        "id": "sector-energy",
        "sector": "Energy",
        "text": (
            "Energy and oil-refining names are directly exposed to crude oil price swings "
            "and government fuel-pricing policy; a crude spike squeezes refining margins "
            "unless the company is a pure upstream producer, in which case the same spike "
            "is a tailwind. Singapore gross refining margins (GRMs) are the specific "
            "benchmark refiners are priced against — a headline crude move can be net "
            "neutral or even positive if GRMs are expanding at the same time. Government "
            "intervention risk is real and recurring: fuel subsidy burden-sharing, windfall "
            "taxes on refiners, and price-cap directives during election cycles have all "
            "been used historically and can override pure market economics for these "
            "stocks. Retail and telecom diversification inside conglomerate energy names "
            "means headline crude-price sensitivity can be materially overstated if those "
            "other segments now contribute the majority of EBITDA."
        ),
    },
    {
        "id": "sector-automobile",
        "sector": "Automobile",
        "text": (
            "Auto sector demand in India is strongly seasonal, with a pronounced "
            "festive-quarter (Sept-Nov) skew, and is rate-sensitive on the financing side "
            "since most two-wheeler and passenger-vehicle purchases are loan-funded. Rural "
            "monsoon outcomes materially swing two-wheeler and tractor volumes, since rural "
            "and semi-urban demand is a large share of entry-level segment sales and "
            "tracks agricultural cash flow closely. Commodity input costs (steel, "
            "aluminium, rubber, and battery-grade lithium/nickel for EVs) are a direct "
            "margin lever largely outside a manufacturer's control. EV transition risk cuts "
            "both ways: legacy internal-combustion-engine product lines face gradual "
            "obsolescence risk, while early EV bets carry real execution and charging-"
            "infrastructure-dependency risk before the segment reaches meaningful scale."
        ),
    },
    {
        "id": "sector-pharmaceuticals",
        "sector": "Pharmaceuticals",
        "text": (
            "Pharma exporters carry US FDA plant-inspection risk — an adverse observation "
            "at a single manufacturing facility can halt that facility's US exports for "
            "months, and the impact is concentrated rather than diversified across a "
            "company's whole product portfolio. India-domestic pricing-control exposure "
            "(NPPA-mandated price caps on essential medicines) is a separate, recurring "
            "constraint on the purely domestic revenue book. Patent-cliff timing for "
            "generics manufacturers is a real, datable catalyst: a major branded drug going "
            "off-patent creates a genuine, quantifiable revenue opportunity window rather "
            "than a vague growth story. R&D pipeline risk for companies pushing into "
            "complex generics or biosimilars is genuinely binary around trial and approval "
            "outcomes, unlike the more gradual risk profile of the commodity-generics "
            "business."
        ),
    },
    {
        "id": "sector-metals",
        "sector": "Metals",
        "text": (
            "Metals and mining names are commodity-cycle plays tied to global demand, "
            "especially from China, and to INR-denominated import/export cost swings; "
            "earnings are lumpy and margin-driven rather than volume-driven in most "
            "quarters. Global steel/aluminium overcapacity, particularly Chinese export "
            "dumping during domestic demand slowdowns, is a recurring structural pressure "
            "on realised prices that domestic producers cannot control. Power and fuel "
            "costs are a large fixed input for smelting-heavy operations, so a domestic "
            "coal or power-tariff shock hits margins directly regardless of the metal price "
            "itself. Environmental and mining-license regulatory risk (renewal delays, "
            "stricter emission norms) can constrain capacity utilisation independent of "
            "demand conditions."
        ),
    },
    {
        "id": "sector-realty",
        "sector": "Realty",
        "text": (
            "Real-estate developer stocks are highly leveraged to home-loan interest "
            "rates and to state-level regulatory (RERA) approval timelines for new project "
            "launches. Inventory overhang — unsold completed or near-completed units — is a "
            "slower-moving but persistent risk that doesn't show up as sharply in quarterly "
            "numbers as it does in eventual cash-flow timing. Land-bank valuation is often "
            "the single largest, and least liquid, asset on a developer's balance sheet, "
            "making book-value-based comparisons unreliable across companies with "
            "different land-acquisition vintages. Construction-input cost inflation "
            "(cement, steel, labour) directly compresses project-level margins on "
            "already-launched projects sold at fixed prices, unlike businesses that can "
            "reprice more easily."
        ),
    },
    {
        "id": "sector-fmcg",
        "sector": "FMCG",
        "text": (
            "FMCG names are typically defensive with lower volatility than the broader "
            "market, but rural demand weakness is a real, recurring swing factor since "
            "rural India is a disproportionately large volume contributor for mass-market "
            "categories. Input-cost inflation, particularly palm oil and other crude "
            "derivatives used across food and personal-care categories, is the primary "
            "margin lever, and companies typically pass it through to consumers with a lag "
            "rather than immediately. Private-label and regional competition has "
            "intensified in several categories, pressuring volume growth for established "
            "national brands even when overall category demand is healthy. Distribution "
            "reach into rural and semi-urban markets is a genuine, hard-to-replicate "
            "competitive moat for incumbents, making market-share loss to new entrants a "
            "slower process here than in urban-centric categories."
        ),
    },
    {
        "id": "general-quality-of-earnings",
        "sector": "general",
        "text": (
            "A single-quarter earnings beat or miss driven by a one-off item — an asset "
            "sale, a tax credit, an impairment reversal — should be weighted less than a "
            "trend visible over two to three consecutive quarters, since one-offs by "
            "definition don't recur. Working-capital movements (receivable days, inventory "
            "days) that diverge sharply from a company's own historical pattern are worth "
            "more scrutiny than the headline profit number, since they often surface real "
            "operating stress before it hits the income statement."
        ),
    },
    {
        "id": "general-liquidity-exit",
        "sector": "general",
        "text": (
            "A stock's average daily traded volume relative to a target position size "
            "determines real exit risk — a thesis can be correct and still be practically "
            "unrealisable in profit if the position can't be unwound without moving the "
            "price against you. Thin free float, high promoter or institutional "
            "concentration, and low daily turnover compound this risk together rather than "
            "independently, and this risk is typically worse in a broad market sell-off, "
            "exactly when an investor is most likely to want to exit."
        ),
    },
    {
        "id": "general-index-flows",
        "sector": "general",
        "text": (
            "Stocks near Nifty index inclusion or exclusion thresholds can see mechanical "
            "passive-fund flows around each scheduled index rebalance date that are "
            "unrelated to the company's underlying fundamentals, which can temporarily "
            "distort both the price and the trading volume used to judge liquidity. FII "
            "(foreign institutional investor) ownership caps and sectoral foreign-"
            "investment limits can also mechanically restrict further passive or active "
            "foreign buying once a stock nears its cap, independent of investor demand."
        ),
    },
    {
        "id": "general-governance",
        "sector": "general",
        "text": (
            "A high or rising promoter share-pledge percentage is a red flag independent "
            "of headline fundamentals — a forced margin-call sale during a broad market "
            "decline can hit the stock hard regardless of the underlying business quality. "
            "Related-party transactions, frequent auditor changes, and delayed or restated "
            "financial filings are governance red flags that historically precede more "
            "serious fundamental problems more often than they occur in isolation."
        ),
    },
]

# ---------------------------------------------------------------------
# Chunking — real overlapping sentence-window chunks, not whole documents.
# ---------------------------------------------------------------------
_CHUNK_SENTENCES = 2   # sentences per chunk
_CHUNK_STRIDE = 1      # sentence overlap between consecutive chunks


def _split_sentences(text: str) -> list:
    # Simple, dependency-free sentence split — good enough for the curated,
    # well-punctuated source paragraphs above.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _chunk_document(doc: dict) -> list:
    sentences = _split_sentences(doc["text"])
    chunks = []
    i = 0
    idx = 0
    while i < len(sentences):
        window = sentences[i:i + _CHUNK_SENTENCES]
        if not window:
            break
        chunks.append({
            "id": f"{doc['id']}-chunk{idx}",
            "sector": doc["sector"],
            "doc_id": doc["id"],
            "text": " ".join(window),
        })
        idx += 1
        i += _CHUNK_STRIDE if len(window) == _CHUNK_SENTENCES else _CHUNK_SENTENCES
    return chunks


_collection = None
_init_failed = False


def _get_collection():
    global _collection, _init_failed
    if _collection is not None or _init_failed:
        return _collection
    try:
        import chromadb
        client = chromadb.PersistentClient(path=STORE_DIR)
        # Versioned collection name — bumping this forces a clean re-index
        # when the chunking/source docs change, rather than silently mixing
        # old and new chunks in the same collection.
        collection = client.get_or_create_collection("knowledge_base_v2")
        if collection.count() == 0:
            all_chunks = [c for doc in _SOURCE_DOCS for c in _chunk_document(doc)]
            collection.add(
                documents=[c["text"] for c in all_chunks],
                metadatas=[{"sector": c["sector"], "doc_id": c["doc_id"]} for c in all_chunks],
                ids=[c["id"] for c in all_chunks],
            )
        _collection = collection
    except Exception:
        _init_failed = True
        _collection = None
    return _collection


# ---------------------------------------------------------------------
# Query rewriting — build the retrieval query from real stock context,
# not one fixed template reused for every stock in a sector.
# ---------------------------------------------------------------------
def _build_query(sector: str, event_type: str = None, risk_flags: list = None) -> str:
    parts = [f"key risk factors for the {sector} sector in Indian equities"]
    if event_type and event_type.lower() not in ("none", "n/a", "unknown", ""):
        parts.append(f"in the context of a recent {event_type} event")
    if risk_flags:
        parts.append("specifically concerning " + ", ".join(risk_flags))
    return " ".join(parts)


def retrieve_for_stock(sector: str, event_type: str = None, risk_flags: list = None,
                        k_sector: int = 2, k_general: int = 1) -> list:
    """
    Real retrieval: metadata-filters to this stock's sector for sector-
    specific chunks, separately metadata-filters to "general" for
    cross-sector risk factors, and rewrites the query text from the
    stock's own real context (event type, risk flags) rather than reusing
    one fixed template for every stock. Returns up to (k_sector + k_general)
    passages, or [] if retrieval isn't available or no sector was given.
    """
    if not sector:
        return []
    collection = _get_collection()
    if collection is None:
        return []

    query_text = _build_query(sector, event_type, risk_flags)
    passages = []
    try:
        sector_results = collection.query(
            query_texts=[query_text],
            n_results=k_sector,
            where={"sector": sector},
        )
        passages.extend(sector_results.get("documents", [[]])[0])
    except Exception:
        pass

    if sector != "general" and k_general > 0:
        try:
            general_results = collection.query(
                query_texts=[query_text],
                n_results=k_general,
                where={"sector": "general"},
            )
            passages.extend(general_results.get("documents", [[]])[0])
        except Exception:
            pass

    return passages


def retrieve_for_sector(sector: str, k: int = 2) -> list:
    """Backwards-compatible thin wrapper around retrieve_for_stock() for
    callers that only have a sector, no event context yet."""
    return retrieve_for_stock(sector, k_sector=k, k_general=0)
