"""
Tiered PII detection: known_pii (exact) → regex + secrets → NER (fallback).

All detected entities are pseudonymized via SessionMap.replacement, which
delegates to the deterministic Faker-based pseudonymizer.
"""

import logging
import os
import re
from typing import TYPE_CHECKING, Callable

import yaml

from secret_scan import find_secrets

if TYPE_CHECKING:
    from session_map import SessionMap

logger = logging.getLogger("pii-proxy.anonymizer")


# ── Structured PII patterns ───────────────────────────────────────────────────

PATTERNS = {
    "EMAIL":       re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PHONE":       re.compile(r"\b(?:\+?1[\s\-.]?)?(?:\(\d{3}\)|\d{3})[\s\-.]?\d{3}[\s\-.]?\d{4}\b"),
    "SSN":         re.compile(r"\b\d{3}[‑\-]\d{2}[‑\-]\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d{4}[\s\-]?){3,4}\d{1,4}\b"),
    # RFC-1918 private ranges only — public IPs are low-risk and noisy
    "IP_ADDRESS":  re.compile(
        r'\b(?:'
        r'10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
        r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
        r'|192\.168\.\d{1,3}\.\d{1,3}'
        r')\b'
    ),
    "ZIP_CODE":    re.compile(r"\b\d{5}(?:[‑\-]\d{4})?\b"),
    # "URL":       re.compile(r"https?://[^\s]+"),  # disabled — low-risk; tokens in URLs are caught by secret_scan
}

# ── Per-label structural validators ──────────────────────────────────────────

def _luhn(s: str) -> bool:
    digits = [int(c) for c in re.sub(r'\D', '', s)]
    if not digits:
        return False
    total = sum(
        (d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
        for i, d in enumerate(reversed(digits))
    )
    return total % 10 == 0

_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "CREDIT_CARD": _luhn,
}

# ── NER configuration ────────────────────────────────────────────────────────

# ORG/FAC/MONEY excluded — too noisy on technical content
_NER_LABELS = {"PERSON", "GPE", "LOC"}

# Single-word common false positives: model names, tech terms, OS names
_NER_BLOCKLIST = {
    "claude", "anthropic", "api", "sdk", "cli", "mcp", "llm", "ai",
    "github", "git", "npm", "pip", "python", "bash", "zsh",
    "darwin", "linux", "macos", "windows",
    "opus", "sonnet", "haiku",
    "shell", "terminal",
}


def _should_anonymize_ent(ent) -> bool:
    text = ent.text.strip()
    if ent.label_ == "PERSON":
        words = [w for w in text.split() if w.rstrip(".")]
        if len(words) < 2:  # require at least two name tokens
            return False
    if text.lower() in _NER_BLOCKLIST:
        return False
    if len(text) < 3:
        return False
    return True


# ── spaCy loader ─────────────────────────────────────────────────────────────

def load_nlp():
    try:
        import spacy
        for model in ("en_core_web_lg", "en_core_web_sm"):
            try:
                nlp = spacy.load(model)
                if model != "en_core_web_lg":
                    print(f"[pii-proxy] Using {model}; for better accuracy: python -m spacy download en_core_web_lg")
                return nlp
            except OSError:
                continue
        print("[pii-proxy] No spaCy model found — falling back to regex only")
    except ImportError:
        print("[pii-proxy] spaCy not installed — falling back to regex only")
    return None


# ── Known PII loader ─────────────────────────────────────────────────────────

def load_known_pii(path: str) -> list[tuple[str, str]]:
    """Return [(label, value), ...] from YAML, or [] if file missing/empty."""
    if not os.path.exists(path):
        logger.info("no known_pii at %s — running with regex + NER only", path)
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("could not parse %s: %s — running without known_pii", path, e)
        return []

    entries: list[tuple[str, str]] = []

    ident = data.get("identity") or {}
    for v in ident.get("names", []) or []:       entries.append(("PERSON", v))
    for v in ident.get("emails", []) or []:      entries.append(("EMAIL", v))
    for v in ident.get("phones", []) or []:      entries.append(("PHONE", v))
    for v in ident.get("addresses", []) or []:   entries.append(("ADDRESS", v))

    emp = data.get("employer") or {}
    for v in emp.get("names", []) or []:         entries.append(("EMPLOYER", v))
    for v in emp.get("domains", []) or []:       entries.append(("EMPLOYER", v))

    for member in data.get("family", []) or []:
        for v in (member or {}).get("names", []) or []:
            entries.append(("PERSON", v))

    for proj in data.get("projects", []) or []:
        if not proj:
            continue
        if proj.get("codename"):
            entries.append(("ORG", proj["codename"]))
        if proj.get("real_name"):
            entries.append(("ORG", proj["real_name"]))

    for v in data.get("ignore", []) or []:
        if v:
            entries.append(("IGNORE", str(v)))

    logger.info("loaded %d known_pii entries from %s", len(entries), path)
    return entries


# ── Replacement helper ───────────────────────────────────────────────────────

def _apply(text: str, original: str, fake: str) -> str:
    """Replace original with fake using word boundaries when safe."""
    if original and original[0].isalnum() and original[-1].isalnum():
        return re.sub(r"\b" + re.escape(original) + r"\b", lambda _: fake, text)
    return text.replace(original, fake)




# ── File-path and localhost-URL exemptions ─────────────────────────────────────

# Matches the username segment in /Users/<name>/ or /home/<name>/ paths
_USER_PATH_RE = re.compile(r'(/(?:Users|home)/)([^/\x00]+)(?=/)')

_LOCAL_HOST_RE = re.compile(
    r'^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|::1)(?::\d+)?$'
)


def _mask_path_usernames(text: str) -> tuple[str, dict]:
    """Replace /Users/<name>/ username segments with opaque tokens."""
    tokens: dict[str, str] = {}
    for i, m in enumerate(reversed(list(_USER_PATH_RE.finditer(text)))):
        tok = f'\x00U{i}\x00'
        tokens[tok] = m.group(2)
        text = text[:m.start(2)] + tok + text[m.end(2):]
    return text, tokens


def _unmask_path_usernames(text: str, tokens: dict) -> str:
    for tok, val in tokens.items():
        text = text.replace(tok, val)
    return text


def _is_local_url(value: str) -> bool:
    m = re.match(r'https?://([^/?#:]+)', value)
    return bool(m and _LOCAL_HOST_RE.match(m.group(1)))


def _is_local_address(value: str) -> bool:
    """True for loopback URLs and bare IPs — exempt regardless of detection label."""
    return _is_local_url(value) or bool(_LOCAL_HOST_RE.match(value))

# ── Main entry point ─────────────────────────────────────────────────────────

def anonymize_text(
    text: str,
    nlp,
    smap: "SessionMap",
    known_pii: list[tuple[str, str]] | None = None,
) -> tuple[str, dict]:
    """Return (anonymized_text, {original: fake}) using the tiered pipeline."""
    # Protect /Users/<name>/ path usernames from anonymization
    text, _path_tokens = _mask_path_usernames(text)

    # Collect (label, original) candidates from all stages without mutating text.
    candidates: list[tuple[str, str]] = []

    # Stage 1: known_pii exact-match (highest priority)
    if known_pii:
        for label, value in known_pii:
            if value and value in text:
                candidates.append((label, value))

    # Stage 2a: structured regex
    for label, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            candidates.append((label, m.group()))

    # Stage 2b: secrets
    for label, original in find_secrets(text):
        candidates.append((label, original))

    # Stage 3: NER fallback (only when nlp is provided — caller decides scope)
    if nlp:
        for ent in nlp(text).ents:
            if ent.label_ in _NER_LABELS and _should_anonymize_ent(ent):
                candidates.append((ent.label_, ent.text))

    # Build ignore set from IGNORE-labelled entries
    ignore_set = {v for label, v in (known_pii or []) if label == "IGNORE"}

    # Drop any candidate whose value is in the ignore set
    if ignore_set:
        candidates = [(l, o) for l, o in candidates if o not in ignore_set]

    # Exempt loopback addresses (URLs and bare IPs) regardless of label — includes CACHED map-replay entries
    candidates = [(l, v) for l, v in candidates if not _is_local_address(v)]

    # Drop candidates that fail structural validation (e.g. Luhn for credit cards)
    candidates = [(l, v) for l, v in candidates
                  if not (vfn := _VALIDATORS.get(l)) or vfn(v)]

    # Build set of explicit known_pii values — these override the reverse-map skip below
    _known_pii_values = {v for lbl, v in (known_pii or []) if lbl != "IGNORE"}

    # Dedupe — first occurrence wins (so known_pii > regex > NER for the same string).
    # Also skip values already in smap.reverse (known fakes) to prevent cascading
    # pseudonymization where generated fakes get re-anonymized on later turns.
    seen = set()
    unique: list[tuple[str, str]] = []
    for label, original in candidates:
        if original in seen:
            continue
        if original not in _known_pii_values and original in smap.reverse:
            continue
        seen.add(original)
        unique.append((label, original))

    # Apply longest-first so "John Smith" gets replaced before "John"
    unique.sort(key=lambda x: -len(x[1]))

    replacements: dict[str, str] = {}
    for label, original in unique:
        if original not in text:
            continue  # swallowed by a longer earlier replacement
        fake = smap.replacement(label, original)
        text = _apply(text, original, fake)
        replacements[original] = fake

    # Restore masked path components
    text = _unmask_path_usernames(text, _path_tokens)
    return text, replacements
