"""
memory_layer.py  —  "The Notebook", a multi-type memory framework
=====================================================================
Three distinct memory types, not one undifferentiated log:

  1. RAW PERSISTED HISTORY — one structured record per stock per run
     (date, real price, scores, action), the durable store everything
     else here is built from.
  2. SHORT-TERM DELTA — build_delta() compares today's real price/scores
     to the SINGLE last call for this symbol: "RSI 54 -> 67, price +5.2%
     since last call" — what changed since we last looked.
  3. CONSOLIDATED TREND — consolidate_trend() rolls up the last N calls
     (not just the last one) into an explicit directional summary per
     metric (price/fundamental/technical/risk each independently
     classified up/down/flat), so the Decision Agent can see "3 straight
     calls of improving technicals" — a pattern only visible across
     multiple records, which the single-step delta above can't show.
  4. PERSISTED GOAL TRACKING — goal_status() is distinct from the trend
     above: it doesn't summarise what happened, it resurfaces a GOAL the
     Decision Agent itself set on a PRIOR call (its own real target_price
     and invalidation condition for this symbol) so a later run can
     check real, current data against it — across separate days/runs,
     not within one run's retry loop (that's a different mechanism —
     see agents/orchestrator_agent.py's `feedback` field, which tracks a
     goal only across retries of the SAME call). The split here is
     deliberate: this module does the deterministic part only (does an
     open goal exist, what's the real price move against its target) —
     never an LLM. Whether the natural-language invalidation condition
     has actually been triggered given today's real event/data stays the
     Decision Agent's judgment call, same plain-code/LLM split as
     everywhere else in this codebase.

Uses ChromaDB if installed (storing each record as JSON in the document
field), or falls back to a plain JSON file. Either way the interface is:

    mem.build_delta(symbol, current_price, current_scores) -> str
    mem.consolidate_trend(symbol, n=5) -> str
    mem.goal_status(symbol, current_price) -> str
    mem.store(symbol, record: dict)
"""

import os
import json
import re
import datetime as dt

import observability
from schemas import MemoryRecord, ValidationError

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

    def _recent_records(self, symbol: str, n: int) -> list:
        """The last n structured records for this symbol, oldest first, or
        [] if there's no history yet."""
        if self.backend == "chroma":
            try:
                res = self._collection.get(where={"symbol": symbol})
                docs = res.get("documents") or []
            except Exception:
                return []
            records = []
            for d in docs:
                try:
                    records.append(json.loads(d))
                except Exception:
                    continue
            records.sort(key=lambda r: r.get("date", ""))
            return records[-n:]
        records = self._data.get(symbol, [])
        return records[-n:]

    def consolidate_trend(self, symbol: str, n: int = 5) -> str:
        """
        Real consolidation logic across the last n real calls — a pattern
        the single-step delta above can't show, since it only ever
        compares against one prior record. Classifies each real metric
        (price, fundamental/technical/risk scores) as trending up, down,
        or flat across those n calls, using the metric's own actual first
        and last values — not an LLM's summary of the numbers.

        The price trend also gets a real MOMENTUM read (needs >= 4
        records): comparing the first half's move to the second half's
        catches acceleration/deceleration/reversal that a plain first-to-
        last comparison can't — a steady climb and a sharp recent
        reversal can show the identical overall change, and only looking
        at the shape across the whole history tells them apart.

        Returns "" (not a placeholder string) if there are fewer than 2
        records, since a trend genuinely can't be computed from one point.
        """
        records = self._recent_records(symbol, n)
        if len(records) < 2:
            return ""

        def _trend(values: list, flat_pct: float = 1.0) -> str:
            first, last = values[0], values[-1]
            if first in (None, 0):
                return "n/a"
            change_pct = (last - first) / abs(first) * 100
            if change_pct > flat_pct:
                return f"up {change_pct:+.1f}%"
            if change_pct < -flat_pct:
                return f"down {change_pct:+.1f}%"
            return "flat"

        def _momentum(values: list) -> str:
            if len(values) < 4:
                return ""
            mid = len(values) // 2
            first_leg = values[mid] - values[0]
            second_leg = values[-1] - values[mid]
            if first_leg == 0 or second_leg == 0:
                return ""
            if (first_leg > 0) != (second_leg > 0):
                return ", momentum: reversed direction in the more recent calls"
            if abs(second_leg) > abs(first_leg) * 1.3:
                return ", momentum: accelerating"
            if abs(second_leg) < abs(first_leg) * 0.7:
                return ", momentum: decelerating"
            return ""

        bits = [f"{len(records)} calls, {records[0].get('date', '?')} -> {records[-1].get('date', '?')}"]

        prices = [r.get("price") for r in records if r.get("price") is not None]
        if len(prices) >= 2:
            bits.append(f"price trend: {_trend(prices)} (₹{prices[0]:.2f} -> ₹{prices[-1]:.2f}){_momentum(prices)}")

        for label, key in (("fundamental", "fundamental_score"), ("technical", "technical_score"), ("risk", "risk_score")):
            scores = [r.get(key) for r in records if r.get(key) is not None]
            if len(scores) >= 2:
                bits.append(f"{label} trend: {_trend(scores, flat_pct=0.01)} ({scores[0]} -> {scores[-1]})")

        return f"{symbol}: " + "; ".join(bits)

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

    def goal_status(self, symbol: str, current_price: float = None, lookback: int = 20) -> str:
        """
        Real, persisted goal tracking across separate runs — see the
        module docstring's item 4. Looks back further than
        consolidate_trend()'s default window (20 calls, not 5): a goal
        set 8 calls ago is still open and worth resurfacing even if it's
        aged out of the recent-trend summary.

        Deterministic only: finds the most recent record that actually
        carries a target_price/invalidation (older records before this
        feature shipped won't have either field, and that's fine — they're
        just skipped), then reports the real, computed price move from
        that call's own entry price to `current_price` against that same
        call's own target_price. Never invents a verdict on whether the
        goal was "met" — a target_price crossing is a real, computed fact
        reported as-is; whether the qualitative invalidation condition
        applies given today's real data is left to the caller (the
        Decision Agent, an LLM) to judge.

        Returns "" if there's no open goal on record for this symbol.
        """
        for rec in reversed(self._recent_records(symbol, lookback)):
            target = rec.get("target_price")
            invalidation = rec.get("invalidation")
            if not target and not invalidation:
                continue
            bits = [f"On {rec.get('date', '?')} this stock was given"]
            entry_price = rec.get("price")
            if target:
                bits.append(f"a target price of ₹{target:.2f}")
                if current_price:
                    if current_price >= target:
                        bits.append(f"— today's real price (₹{current_price:.2f}) has now REACHED OR PASSED it")
                    else:
                        gap_pct = (target - current_price) / current_price * 100
                        bits.append(f"— today's real price (₹{current_price:.2f}) is {gap_pct:.1f}% away from it")
            if entry_price and current_price:
                move_pct = (current_price - entry_price) / entry_price * 100
                bits.append(f"(₹{entry_price:.2f} -> ₹{current_price:.2f}, {move_pct:+.1f}% since that call)")
            if invalidation:
                bits.append(f'. Its stated invalidation condition was: "{invalidation}" '
                             f"— judge from today's real data whether that has now happened.")
            return " ".join(bits)
        return ""

    _NUMERIC_FIELDS = ("price", "fundamental_score", "technical_score", "risk_score", "target_price")

    @staticmethod
    def _repair_numeric_fields(record: dict) -> dict:
        """Real repair, not just validate-then-reject: a numeric field
        given as a decorated string ("₹1,292.50", "70%", " 7.0 ") has an
        unambiguous real value underneath — strip the decoration and
        coerce to float before the typed check runs. A value that still
        doesn't parse as a number is left alone, so MemoryRecord's own
        validation (and the RuntimeError above) is what reports it."""
        repaired = dict(record)
        for field in MemoryLayer._NUMERIC_FIELDS:
            value = repaired.get(field)
            if isinstance(value, str):
                stripped = re.sub(r"[^\d.\-]", "", value)
                try:
                    repaired[field] = float(stripped)
                except ValueError:
                    pass  # leave as-is — let MemoryRecord's validation report it
        return repaired

    def store(self, symbol: str, record: dict):
        """Save one structured record: {date, price, fundamental_score, technical_score, risk_score, action}.

        Validated against schemas.MemoryRecord before it's written — a
        malformed record never gets stored: an early, loud RuntimeError
        beats a record that quietly corrupts every
        consolidate_trend()/build_delta() read of this symbol from here
        on. A numeric field given as a decorated string (e.g. "₹1,292.50"
        or "70%") is REPAIRED — the real value is unambiguous — before
        that check runs; only a value that still can't be coerced after
        repair is refused."""
        record = {"date": dt.date.today().isoformat(), **record}
        record = self._repair_numeric_fields(record)
        try:
            MemoryRecord.model_validate(record)
        except ValidationError as e:
            observability.record_failure(
                "memory_record_shape", f"refused to store a malformed record for {symbol}: {e}",
                symbol=symbol,
            )
            raise RuntimeError(f"[memory] refusing to store a malformed record for {symbol}: {e}") from e
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
