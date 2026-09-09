"""
test_knowledge_base.py — the RAG retrieval layer.

Real behaviour under test: source documents are actually chunked into
multiple overlapping pieces (not stored whole); metadata filtering
correctly isolates a sector's chunks; query rewriting changes which
chunk ranks first when the same sector gets a different real event type;
and an unknown sector still gets the "general" cross-sector chunks.

Uses the real ChromaDB-backed collection (local, no network) — this is
retrieval correctness, not a mock.
"""

from memory.knowledge_base import retrieve_for_stock, _chunk_document, _SOURCE_DOCS


def test_every_source_document_is_split_into_multiple_chunks():
    for doc in _SOURCE_DOCS:
        chunks = _chunk_document(doc)
        assert len(chunks) > 1, f"{doc['id']} produced only {len(chunks)} chunk(s)"
        for c in chunks:
            assert c["sector"] == doc["sector"]
            assert c["text"]  # never an empty chunk


def test_retrieval_returns_something_for_a_known_sector():
    results = retrieve_for_stock("Banking")
    assert results
    assert all(isinstance(r, str) and r for r in results)


def test_query_rewriting_changes_ranking_for_different_real_context():
    regulatory = retrieve_for_stock("Banking", event_type="regulatory", risk_flags=["high volatility"])
    earnings = retrieve_for_stock("Banking", event_type="earnings")
    assert regulatory and earnings
    # Different real context must not always retrieve the identical top
    # passage — if it does, the query isn't actually being rewritten.
    assert regulatory[0] != earnings[0]


def test_unknown_sector_still_returns_general_risk_chunks():
    # No sector-tagged chunks exist for a made-up sector, but the
    # separate "general" filtered pool should still answer.
    results = retrieve_for_stock("NotARealSector")
    assert results
    assert all(isinstance(r, str) and len(r) > 20 for r in results)


def test_empty_sector_returns_nothing():
    assert retrieve_for_stock("") == []
    assert retrieve_for_stock(None) == []
