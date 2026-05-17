#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"
PLIST_LABEL="com.jai.pii-proxy"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
ZSHRC="$HOME/.zshrc"

echo "==> Setting up PII proxy in: $PROJECT_DIR"

# ── virtualenv ────────────────────────────────────────────────────────────────
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "==> Creating virtualenv…"
    python3 -m venv "$PROJECT_DIR/venv"
fi

echo "==> Installing Python dependencies…"
"$PROJECT_DIR/venv/bin/pip" install --quiet --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"

echo "==> Downloading spaCy model (en_core_web_lg)…"
"$PYTHON_BIN" -m spacy download en_core_web_lg || {
    echo "    en_core_web_lg failed, trying en_core_web_sm…"
    "$PYTHON_BIN" -m spacy download en_core_web_sm
}

# ── shell env ─────────────────────────────────────────────────────────────────
ENV_LINE='export ANTHROPIC_BASE_URL=http://127.0.0.1:8082'
if ! grep -qF "$ENV_LINE" "$ZSHRC" 2>/dev/null; then
    echo "" >> "$ZSHRC"
    echo "# PII anonymization proxy" >> "$ZSHRC"
    echo "$ENV_LINE" >> "$ZSHRC"
    echo "==> Added ANTHROPIC_BASE_URL to $ZSHRC"
else
    echo "==> ANTHROPIC_BASE_URL already set in $ZSHRC"
fi

# ── launchd plist ─────────────────────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${PROJECT_DIR}/pii_proxy.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/pii-proxy.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/pii-proxy.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST

echo "==> Wrote launchd plist: $PLIST_PATH"

# unload first in case it was already loaded
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
echo "==> launchd service loaded"

echo ""
echo "✅  PII proxy installed and running."
echo ""
echo "    Restart your terminal, or run:"
echo "      source ~/.zshrc"
echo ""
echo "    Verify the proxy is up:"
echo "      curl http://localhost:8082/health"
echo ""
echo "    Logs: tail -f /tmp/pii-proxy.log /tmp/pii-proxy.err"
