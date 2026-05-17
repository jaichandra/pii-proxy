import os

ANTHROPIC_BASE = "https://api.anthropic.com"
PORT = 8082
SESSION_TTL = 3600   # seconds; idle sessions older than this are evicted
LOG_LEVEL = "INFO"

PII_HOME = os.path.expanduser("~/.pii-proxy")
KNOWN_PII_PATH = os.path.join(PII_HOME, "known_pii.yaml")
MAP_PATH = os.path.join(PII_HOME, "map.json")
