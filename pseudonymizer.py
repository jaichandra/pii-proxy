"""
Deterministic pseudonym generator using Faker.

fake_for(label, original) returns a stable realistic-looking fake value for the
given original. Same original → same fake, always. This is what enables prompt
cache hits at Anthropic and stable cross-restart behavior.
"""

import hashlib
import re
import string

from faker import Faker


def _seed(original: str) -> int:
    return int(hashlib.md5(original.encode("utf-8")).hexdigest()[:8], 16)


def _fake(original: str) -> Faker:
    f = Faker()
    f.seed_instance(_seed(original))
    return f


def _rand_chars(n: int, alphabet: str, fake: Faker) -> str:
    return "".join(fake.random.choices(alphabet, k=n))


_ALPHA_UPPER_NUM = string.ascii_uppercase + string.digits
_ALPHA_NUM = string.ascii_letters + string.digits
_B64 = string.ascii_letters + string.digits + "-_"


def fake_for(label: str, original: str) -> str:
    """Deterministic Faker output for the given label/original."""
    f = _fake(original)

    if label == "PERSON":
        return f.name()
    if label == "EMAIL":
        return f.email()
    if label == "PHONE":
        return f.phone_number()
    if label == "SSN":
        return f.ssn()
    if label == "CREDIT_CARD":
        return f.credit_card_number()
    if label == "IP_ADDRESS":
        return f.ipv4()
    if label in ("GPE", "LOC"):
        return f.city()
    if label == "ADDRESS":
        return f.address().replace("\n", ", ")
    if label == "ZIP_CODE":
        return f.zipcode()
    if label == "URL":
        return f.url().rstrip("/")
    if label == "DATE":
        return f.date()
    if label.startswith("SECRET_"):
        return _fake_secret(label, original, f)
    if label in ("ORG", "EMPLOYER"):
        return f.company()

    # generic catch-all: same length, ASCII alphanumeric, preserving '@' or '.' if present
    if "@" in original:
        return f.email()
    return _rand_chars(max(8, len(original)), _ALPHA_NUM, f)


def _fake_secret(label: str, original: str, f: Faker) -> str:
    """Generate a fake secret matching the structural shape of the original."""
    if label == "SECRET_AWS_KEY":
        return "AKIA" + _rand_chars(16, _ALPHA_UPPER_NUM, f)
    if label == "SECRET_GITHUB_PAT":
        return "ghp_" + _rand_chars(36, _ALPHA_NUM, f)
    if label == "SECRET_GITHUB_OAUTH":
        return "gho_" + _rand_chars(36, _ALPHA_NUM, f)
    if label == "SECRET_SLACK_TOKEN":
        # preserve the xox?- prefix from the original
        m = re.match(r"^(xox[bpars]-)", original)
        prefix = m.group(1) if m else "xoxb-"
        return prefix + _rand_chars(max(20, len(original) - len(prefix)), _ALPHA_NUM, f)
    if label == "SECRET_JWT":
        # three base64 segments matching original section lengths
        parts = original.split(".")
        return ".".join(_rand_chars(max(20, len(p)), _B64, f) for p in parts) if len(parts) == 3 \
            else _rand_chars(60, _B64, f) + "." + _rand_chars(60, _B64, f) + "." + _rand_chars(40, _B64, f)
    if label == "SECRET_PRIVATE_KEY":
        return ("-----BEGIN FAKE PRIVATE KEY-----\n"
                + _rand_chars(64, _B64, f) + "\n"
                + _rand_chars(64, _B64, f) + "\n"
                + "-----END FAKE PRIVATE KEY-----")
    # SECRET_GENERIC and any unknown SECRET_* — same-length alphanumeric
    return _rand_chars(max(16, len(original)), _ALPHA_NUM, f)
