"""
text_safety.py  —  guardrails for the one real untrusted-input boundary
==========================================================================
Every prompt this app builds is assembled from data it fetched itself
(prices, headlines, its own scores) — except one: an investor's uploaded
profile document (.txt/.md/.pdf/.html/.docx/.xlsx), which is arbitrary
text from outside the system that gets read verbatim and handed to an LLM
(profile/profile_loader.py). That's the one place a prompt-injection
attempt or accidental PII leak can actually reach this app, so it's the
one place a real guardrail belongs — not sprinkled everywhere on data this
app already trusts because it produced it.

Two checks, both real (regex-based, not an LLM call — a formula can
answer "does this look like an email address" just as well as an AI can,
cheaper and deterministically):

    scan_for_injection(text) -> [matched pattern descriptions]
    redact_pii(text)         -> text with emails/phones/PAN numbers masked

sanitize_untrusted_text() combines both: logs any injection attempt found
(observability.record_failure — an audit trail, not a silent block, since
a false positive shouldn't break a legitimate profile), then returns the
PII-redacted text ready to go into a prompt.
"""

import re

import observability

# Patterns aimed at the actual attack shape that matters here: text INSIDE
# an uploaded document trying to override the system prompt or extract
# something it shouldn't. Not exhaustive — a real guardrail service (e.g.
# Bodhi's own guardrailChecks on an agent node) would do more — but this
# catches the common, cheap attempts rather than doing nothing.
_INJECTION_PATTERNS = [
    (re.compile(r"ignore (all )?(previous|prior|above) instructions", re.I), "instruction override attempt"),
    (re.compile(r"disregard (all )?(previous|prior|above)", re.I), "instruction override attempt"),
    (re.compile(r"you are now|act as if you are|pretend to be", re.I), "role override attempt"),
    (re.compile(r"system\s*:\s*", re.I), "embedded fake system-role marker"),
    (re.compile(r"reveal (your |the )?(system prompt|instructions)", re.I), "system-prompt extraction attempt"),
    (re.compile(r"</?(system|assistant|user)>", re.I), "embedded fake chat-role tag"),
]

# India-specific PII patterns, since this app's own documented users are
# Indian equity investors uploading real profile documents.
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[REDACTED_PAN]"),  # Indian PAN card format
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[REDACTED_AADHAAR]"),  # Indian Aadhaar format
]


def scan_for_injection(text: str) -> list:
    """Real pattern matching, not an LLM judgment call — returns a list of
    human-readable descriptions of what matched, or [] if nothing did."""
    if not text:
        return []
    found = []
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            found.append(description)
    return found


def redact_pii(text: str) -> str:
    """Masks obvious PII before it's ever put in a prompt or logged —
    never sent upstream to the LLM gateway, never written to disk."""
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_untrusted_text(text: str, source: str) -> str:
    """The one call site this module expects to be used from: takes raw
    text read from an uploaded profile document, logs an audit record if
    it looks like a prompt-injection attempt (doesn't block — a false
    positive here shouldn't stop someone's real profile from loading, but
    a real attempt should be visible after the fact), then returns the
    text with PII redacted, ready to go into a prompt."""
    issues = scan_for_injection(text)
    if issues:
        observability.record_failure(
            "text_safety_injection_scan",
            f"possible prompt-injection pattern(s) in {source}: {', '.join(issues)}",
            source=source,
        )
    return redact_pii(text)
