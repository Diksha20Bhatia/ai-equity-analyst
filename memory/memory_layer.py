"""
memory_layer.py  —  "The Notebook", now an audit system
=========================================================
Stores a structured record per stock per run (date, real price, scores,
action) instead of a free-text note. On the next run, build_delta() compares
today's real price to the price at the time of the last call and reports
what actually changed — a compact DELTA, not the whole history — so the
Decision Agent can see "RSI 54 -> 67, price +5.2% since last call" instead
of re-reading everything ever said about a stock.

Uses ChromaDB if installed (storing the record as JSON in the document
field), or falls back to a plain JSON file. Either way the interface is:

    mem.build_delta(symbol, current_price) -> str
    mem.store(symbol, record: dict)
"""

import os
import json
import datetime as dt

STORE_DIR = os.path.join(os.path.dirname(__file__), "_store")
JSON_PATH = os.path.join(STORE_DIR, "memory.json")


class MemoryLayer:
    def __init__(self):
        os.makedirs(STORE_DIR, exist_ok=True)
        self.backend = "json"
        self._try_chroma()
        if self.backend == "json":
            self._data = self._load_json()

    # ------------------------------------------------------------------
    def _try_chroma(self):
        try:
            import chromadb
            self._chroma_client = chromadb.PersistentClient(path=STORE_DIR)
            self._collection = self._chroma_client.get_or_create_collection("analysis")
            self.backend = "chroma"
        except Exception:
            self.backend = "json"

    def _load_json(self):
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_json(self):
        with open(JSON_PATH, "w") as f:
            json.dump(self._data, f, indent=2)

    # ------------------------------------------------------------------
    def _last_record(self, symbol: str) -> dict:
        """Most recent structured record stored for this symbol, or {}."""
        if self.backend == "chroma":
            try:
                res = self._collection.get(where={"symbol": symbol})
                docs = res.get("documents") or []
            except Exception:
                return {}
            records = []
            for d in docs:
                try:
                    records.append(json.loads(d))
                except Exception:
                    continue  # skip records from an older, non-JSON memory format
            if not records:
                return {}
            records.sort(key=lambda r: r.get("date", ""))
            return records[-1]
        records = self._data.get(symbol, [])
        return records[-1] if records else {}

    def build_delta(self, symbol: str, current_price: float = None, current_scores: dict = None) -> str:
        """
        Real comparison against the last stored call for this symbol.
        Returns a one-line summary; "No prior context" if this is the first
        time we've seen it.
        """
        prev = self._last_record(symbol)
        if not prev:
            return f"{symbol}: no prior context."

        bits = [f"Previous call ({prev.get('date', '?')}): {prev.get('action', 'n/a')}"]
        prev_price = prev.get("price")
        if prev_price and current_price:
            ret = round((current_price - prev_price) / prev_price * 100, 1)
            bits.append(f"price {prev_price:.2f} -> {current_price:.2f} ({ret:+.1f}% since)")

        if current_scores:
            for label, key in (("fundamental", "fundamental_score"), ("technical", "technical_score"), ("risk", "risk_score")):
                if key in prev and key in current_scores:
                    bits.append(f"{label} {prev[key]} -> {current_scores[key]}")

        return f"{symbol}: " + "; ".join(bits)

    def store(self, symbol: str, record: dict):
        """Save one structured record: {date, price, fundamental_score, technical_score, risk_score, action}."""
        record = {"date": dt.date.today().isoformat(), **record}
        if self.backend == "chroma":
            try:
                self._collection.add(
                    documents=[json.dumps(record)],
                    metadatas=[{"symbol": symbol, "date": record["date"]}],
                    ids=[f"{symbol}-{record['date']}-{dt.datetime.now().timestamp()}"],
                )
                return
            except Exception:
                pass  # fall through to JSON if chroma write fails
        self._data.setdefault(symbol, []).append(record)
        self._save_json()
