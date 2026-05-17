"""
Credential / secret scanner.

find_secrets(text) yields (label, original) tuples for every secret-looking
token. The caller pseudonymizes each via SessionMap.replacement, same as PII.
"""

import re

# Explicit, high-confidence patterns. Each tuple: (label, compiled pattern).
SECRET_PATTERNS = [
    ("SECRET_AWS_KEY",       re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SECRET_AWS_SESSION",   re.compile(r"\bASIA[0-9A-Z]{16}\b")),
    ("SECRET_GITHUB_PAT",    re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("SECRET_GITHUB_OAUTH",  re.compile(r"\bgho_[A-Za-z0-9]{36}\b")),
    ("SECRET_GITHUB_USER",   re.compile(r"\bghu_[A-Za-z0-9]{36}\b")),
    ("SECRET_GITHUB_SERVER", re.compile(r"\bghs_[A-Za-z0-9]{36}\b")),
    ("SECRET_SLACK_TOKEN",   re.compile(r"\bxox[bpars]-[A-Za-z0-9-]{20,}\b")),
    ("SECRET_JWT",           re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("SECRET_PRIVATE_KEY",   re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("SECRET_STRIPE_LIVE",   re.compile(r"\bsk_live_[A-Za-z0-9]{24,}\b")),
    ("SECRET_OPENAI",        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("SECRET_ANTHROPIC",     re.compile(r"\bsk-ant-(?:api|admin)\d+-[A-Za-z0-9_-]{40,}\b")),
]

# ENV-style: KEY_NAME=value — only the value is the secret. Matches conservatively.
# Requires value ≥ 12 chars to avoid grabbing things like API_KEY=test or KEY=1.
_ENV_SECRET = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL))\s*=\s*[\"']?([^\s\"'\n]{12,})[\"']?"
)


def find_secrets(text: str):
    """Yield unique (label, original) pairs for every secret found in text."""
    seen = set()
    for label, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            original = m.group()
            if original not in seen:
                seen.add(original)
                yield label, original

    for m in _ENV_SECRET.finditer(text):
        value = m.group(2)
        if value not in seen:
            seen.add(value)
            yield "SECRET_GENERIC", value
