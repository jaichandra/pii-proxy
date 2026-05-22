"""
Credential / secret scanner — 24 rules sourced from gitleaks and TruffleHog.

find_secrets(text) yields (label, original) tuples for every secret-looking
token. The caller pseudonymizes each via SessionMap.replacement, same as PII.
"""

import math
import re
from dataclasses import dataclass


def _entropy(s: str) -> float:
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((v / n) * math.log2(v / n) for v in freq.values())


@dataclass
class _Rule:
    label: str
    regex: re.Pattern
    secret_group: int | None = None  # capture group holding the secret; None = full match
    entropy_min: float | None = None  # skip if entropy(secret) < threshold


_RULES: list[_Rule] = [
    # ── Cloud ────────────────────────────────────────────────────────────────
    _Rule(
        "SECRET_AWS_KEY",
        re.compile(r"\b(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"),
    ),
    _Rule(
        "SECRET_GCP_KEY",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    _Rule(
        "SECRET_PRIVATE_KEY",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |PGP |OPENSSH )?PRIVATE KEY"),
    ),

    # ── Source control ───────────────────────────────────────────────────────
    _Rule(
        "SECRET_GITHUB_PAT",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    ),
    _Rule(
        "SECRET_GITHUB_FINE",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
    ),
    _Rule(
        "SECRET_GITLAB_PAT",
        re.compile(r"\bglpat-[A-Za-z0-9_=-]{20,22}\b"),
    ),

    # ── Package registries ───────────────────────────────────────────────────
    _Rule(
        "SECRET_NPM_TOKEN",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    ),

    # ── Communication ────────────────────────────────────────────────────────
    _Rule(
        "SECRET_SLACK_TOKEN",
        re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,72}\b"),
    ),
    _Rule(
        "SECRET_SLACK_WEBHOOK",
        re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]{8,10}/B[A-Za-z0-9_]{8,12}/[A-Za-z0-9_]{23,24}"),
    ),
    _Rule(
        "SECRET_DISCORD_WEBHOOK",
        re.compile(r"https://discord(?:app)?\.com/api/webhooks/[0-9]{17,20}/[A-Za-z0-9_-]{68}"),
    ),
    _Rule(
        "SECRET_TELEGRAM_TOKEN",
        re.compile(r"\b[0-9]{8,10}:AA[0-9A-Za-z_-]{33}\b"),
    ),
    _Rule(
        "SECRET_TWILIO_SID",
        re.compile(r"\bAC[0-9a-f]{32}\b"),
    ),

    # ── Email services ───────────────────────────────────────────────────────
    _Rule(
        "SECRET_SENDGRID_KEY",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{20,24}\.[A-Za-z0-9_-]{39,50}\b"),
    ),
    _Rule(
        "SECRET_MAILGUN_KEY",
        re.compile(r"\bkey-[0-9a-zA-Z]{32}\b"),
    ),
    _Rule(
        "SECRET_MAILCHIMP_KEY",
        re.compile(r"\b[0-9a-f]{32}-us[0-9]{1,2}\b"),
    ),

    # ── Payment ──────────────────────────────────────────────────────────────
    _Rule(
        "SECRET_STRIPE_LIVE",
        re.compile(r"\bsk_(?:live|test)_[0-9a-zA-Z]{24}\b"),
    ),
    _Rule(
        "SECRET_STRIPE_RESTRICTED",
        re.compile(r"\brk_(?:live|test)_[0-9a-zA-Z]{24}\b"),
    ),

    # ── AI services ──────────────────────────────────────────────────────────
    _Rule(
        "SECRET_OPENAI",
        re.compile(r"\bsk-(?!proj-|ant-)[A-Za-z0-9]{48}\b"),
    ),
    _Rule(
        "SECRET_OPENAI_PROJECT",
        re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"),
        entropy_min=3.5,
    ),
    _Rule(
        "SECRET_ANTHROPIC",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{95}\b"),
    ),

    # ── Auth tokens ──────────────────────────────────────────────────────────
    _Rule(
        "SECRET_JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),

    # ── Generic / env-based ──────────────────────────────────────────────────
    _Rule(
        "SECRET_GENERIC",
        re.compile(
            r"(?:api[_-]?key|secret[_-]?key|access[_-]?token|api[_-]?secret)"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9\-_.]{20,})",
            re.IGNORECASE,
        ),
        secret_group=1,
        entropy_min=3.5,
    ),
    _Rule(
        "SECRET_ENV",
        re.compile(
            r"\b[A-Z_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|PRIVATE_KEY)[A-Z_0-9]*"
            r"\s*=\s*(\S{8,})"
        ),
        secret_group=1,
        entropy_min=3.0,
    ),
    _Rule(
        "SECRET_CONNECTION",
        re.compile(r"(?:mongodb|mysql|postgres|postgresql|redis)://[^:\s]+:[^@\s]+@"),
    ),
]


def find_secrets(text: str):
    """Yield unique (label, original) pairs for every secret found in text."""
    seen: set[str] = set()
    for rule in _RULES:
        for m in rule.regex.finditer(text):
            # When secret_group is set, yield the capture group value so that
            # only the value is pseudonymized (KEY= prefix is preserved in text).
            secret_val = m.group(rule.secret_group) if rule.secret_group is not None else m.group()
            if not secret_val:
                continue
            if rule.entropy_min is not None and _entropy(secret_val) < rule.entropy_min:
                continue
            if secret_val not in seen:
                seen.add(secret_val)
                yield rule.label, secret_val
