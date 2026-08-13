"""
profile_loader.py  —  "Your preferences, in any format"
=======================================================
Reads an investor profile from .txt / .md / .pdf / .html / .docx, then uses
Gemini to normalise the messy text into ONE clean structured object the
Decision Agent can act on. If Gemini is unavailable it does a light
keyword-based extraction instead.

Returned shape:
  {
    "risk_appetite": "...",
    "capital_available": "...",
    "investment_horizon": "...",
    "sector_preferences": [...],
    "sector_exclusions": [...],
    "constraints": "..."
  }
"""

import os
import json
from config import client, settings


# ---------------------------------------------------------------------
# Step 1: read raw text out of whatever file format was given.
# ---------------------------------------------------------------------
def _read_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()

    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            print("[profile] pypdf not installed; cannot read PDF.")
            return ""

    if ext in (".html", ".htm"):
        try:
            from bs4 import BeautifulSoup
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return BeautifulSoup(f.read(), "html.parser").get_text(" ")
        except ImportError:
            print("[profile] beautifulsoup4 not installed; cannot read HTML.")
            return ""

    if ext == ".docx":
        try:
            import docx
            d = docx.Document(path)
            return "\n".join(p.text for p in d.paragraphs)
        except ImportError:
            print("[profile] python-docx not installed; cannot read DOCX.")
            return ""

    print(f"[profile] unsupported format: {ext}")
    return ""


# ---------------------------------------------------------------------
# Step 2: turn raw text into a structured profile object.
# ---------------------------------------------------------------------
def _structure_with_gemini(raw: str) -> dict:
    if client is None or not raw.strip():
        return {}
    try:
        from google.genai import types
        prompt = (
            "Read this investor profile and extract the fields below. "
            "If a field is missing, use a sensible default or empty list.\n\n"
            f"PROFILE TEXT:\n{raw}\n\n"
            'Reply as JSON only: {"risk_appetite": "conservative/moderate/aggressive", '
            '"capital_available": "string", "investment_horizon": "short/medium/long", '
            '"sector_preferences": [..], "sector_exclusions": [..], "constraints": "string"}'
        )
        resp = client.models.generate_content(
            model=settings.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        text = (resp.text or "").strip().strip("`")
        if "{" in text:
            text = text[text.find("{"):]
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        print(f"[profile] Gemini structuring failed, using keyword fallback. ({e})")
        return {}


def _structure_with_keywords(raw: str) -> dict:
    low = raw.lower()
    risk = "moderate"
    if "aggressive" in low:
        risk = "aggressive"
    elif "conservative" in low or "low risk" in low:
        risk = "conservative"
    horizon = "long" if "long" in low else "medium"
    exclusions = []
    for sector in ("tobacco", "alcohol", "penny", "f&o", "derivatives", "smallcap"):
        if sector in low:
            exclusions.append(sector)
    return {
        "risk_appetite": risk,
        "capital_available": "",
        "investment_horizon": horizon,
        "sector_preferences": [],
        "sector_exclusions": exclusions,
        "constraints": "",
    }


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------
def load_profile(path: str) -> dict:
    if not path or not os.path.exists(path):
        print("[profile] no profile file found — running without personalisation.")
        return {}
    raw = _read_text(path)
    if not raw.strip():
        return {}
    structured = _structure_with_gemini(raw)
    if not structured:
        structured = _structure_with_keywords(raw)
    print(f"[profile] loaded profile ({structured.get('risk_appetite','?')} risk).")
    return structured
