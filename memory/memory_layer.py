"""
memory_layer.py  —  "The Notebook"
==================================
Remembers what was analysed on previous runs so the system builds context
over time (e.g. "this is the third day RELIANCE shows up").

It tries to use ChromaDB (a vector database) if installed. If not, it quietly
falls back to a plain JSON file. Either way the interface is the same:

    mem.recall(symbol)   -> list of short past notes
    mem.store(symbol, note)
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
        self._chroma = None
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
            self.backend = "json"  # chromadb missing or failed -> JSON

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
    def recall(self, symbol: str, k: int = 3) -> list:
        """Return up to k short notes about this symbol from the past."""
        if self.backend == "chroma":
            try:
                res = self._collection.query(
                    query_texts=[symbol], n_results=k,
                    where={"symbol": symbol},
                )
                return res.get("documents", [[]])[0]
            except Exception:
                return []
        notes = self._data.get(symbol, [])
        return [n["note"] for n in notes[-k:]]

    def store(self, symbol: str, note: str):
        """Save one short note about this symbol."""
        stamp = dt.date.today().isoformat()
        text = f"[{stamp}] {note}"
        if self.backend == "chroma":
            try:
                self._collection.add(
                    documents=[text],
                    metadatas=[{"symbol": symbol, "date": stamp}],
                    ids=[f"{symbol}-{stamp}-{dt.datetime.now().timestamp()}"],
                )
                return
            except Exception:
                pass  # fall through to JSON if chroma write fails
        self._data.setdefault(symbol, []).append({"date": stamp, "note": note})
        self._save_json()
