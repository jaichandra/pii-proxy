import os

ANTHROPIC_BASE = os.environ.get("PII_ANTHROPIC_BASE", "https://api.anthropic.com")
OPENAI_BASE    = os.environ.get("PII_OPENAI_BASE",    "https://api.openai.com")
PORT = 8082
SESSION_TTL = 3600   # seconds; idle sessions older than this are evicted
LOG_LEVEL = "INFO"

PII_HOME = os.path.expanduser("~/.pii-proxy")
KNOWN_PII_PATH = os.path.join(PII_HOME, "known_pii.yaml")
MAP_PATH = os.path.join(PII_HOME, "map.json")

# Extract text from PDF document blocks and run the full detection pipeline on them.
# Tradeoff: PDF is replaced with plain text — Claude loses formatting, images, and layout.
# Requires: pip install pymupdf
PDF_SCAN = os.environ.get("PII_PDF_SCAN", "").lower() in ("1", "true", "yes")
