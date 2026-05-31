# pii-proxy

[License](https://opensource.org/licenses/Apache-2.0)
[Python](https://www.python.org)

A local reverse proxy that intercepts every outgoing request to Anthropic and OpenAI, replaces personal information and credentials with realistic pseudonyms, then restores the real values in responses — so the AI provider never sees your actual PII.

---

## Install

**One-click installer** — download the file for your platform from the [latest release](https://github.com/jaichandra/pii-proxy/releases/latest) and run it. No other files needed.

| Platform | File |
|----------|------|
| macOS | `pii-proxy-installer-mac.command` — double-click in Finder |
| Linux | `pii-proxy-installer-linux.sh` — `bash pii-proxy-installer-linux.sh` |
| Windows | `pii-proxy-installer-windows.bat` — double-click |

The installer sets up a Python virtual environment, downloads dependencies and the spaCy language model, and configures the proxy to start automatically on login. Python 3.9+ is installed automatically if not found.

For a manual setup, see [Quick start](#quick-start) below.

---

## Why pii-proxy

- **Zero changes to your prompts.** Route your AI client through the proxy with one env var. Your workflow stays identical.
- **Deterministic pseudonyms.** The same real value always produces the same fake, keeping the model's reasoning consistent and the upstream prompt cache warm.
- **Full round-trip fidelity.** Responses are de-anonymized before they reach your screen. Tool calls and file writes contain the correct real values.
- **Covers what you forget.** Beyond your explicit PII list, the proxy runs regex (email, phone, SSN, credit card, IP, ZIP, URL), a credential scanner (AWS keys, GitHub tokens, JWTs, Stripe keys, `.env`-style secrets), and spaCy NER — catching names and places you didn't think to list.
- **Multi-provider, single instance.** One proxy handles both Anthropic (`/v1/messages`) and OpenAI (`/v1/chat/completions`) simultaneously.

---

## How it works

```
Claude Code                OpenAI SDK client
    │  ANTHROPIC_BASE_URL=     │  OPENAI_BASE_URL=
    │  http://localhost:8082    │  http://localhost:8082
    └─────────────┬────────────┘
                  ▼
       pii_proxy.py  (aiohttp, port 8082)
                  │
                  ├─ route by path ──────────────────────────────────
                  │      /v1/messages          →  AnthropicProvider
                  │      /v1/chat/completions  →  OpenAIProvider
                  │      everything else       →  pass through untouched
                  │
                  ├─ anonymize request body  ────────────────────────────
                  │      [system prompt]      regex + known_pii                no NER
                  │      [latest user msg]    regex + known_pii + NER          full pipeline
                  │      [history user msgs]  regex + known_pii + map replay   no NER (fast)
                  │      [assistant turns]    regex + known_pii                no NER
                  │      [tool / tool_result] regex + known_pii + map replay   no NER
                  │
                  ├─ forward to upstream API  (pseudonymized request)
                  │
                  ├─ receive response
                  │
                  └─ deanonymize response  →  client sees real values
```

### Detection pipeline

```
Stage 1  known_pii.yaml   exact match (highest precision, zero false positives)
Stage 2a PATTERNS regex   email, phone, SSN, credit card, IP, ZIP, URL
Stage 2b secret_scan      AWS keys, GitHub tokens, Slack tokens, JWT, private keys,
                          Stripe/OpenAI/Anthropic keys, ENV-style KEY=value secrets
Stage 3  spaCy NER        PERSON (≥2 words), GPE, LOC  — latest user message only
Stage 3' map replay       fast string-match against session map  — history messages
```

First match wins — `known_pii > regex > NER` for the same string. Values listed under `ignore:` are exempt from all stages. Replacements are applied longest-first to prevent partial matches (e.g. "John" never clobbers "Johnson").

**NER scoping:** spaCy only runs on the newest user message. All prior user messages and tool results use a fast string-match against the session map — anything NER ever discovered is already stored there, so no coverage is lost and NER cost stays constant regardless of conversation length.

**File path and localhost exemptions:** Username segments inside `/Users/<name>/` and `/home/<name>/` paths are never anonymized — anonymizing them would break file operations. Similarly, `http://localhost` and `127.x.x.x` addresses are exempt from the URL and IP regex stages.

### Pseudonymization

`fake_for(label, original)` seeds Faker with `md5(original)[:8]` so the same real value always produces the same fake.


| Label             | Fake looks like                      |
| ----------------- | ------------------------------------ |
| PERSON            | `Grace Daniels`                      |
| EMAIL             | `espinozasamuel@example.net`         |
| PHONE             | `+737-907-7967x1625`                 |
| ADDRESS           | `USS Steele, FPO AE 51334`           |
| EMPLOYER / ORG    | `Steele, Bond and Huff`              |
| SECRET_AWS_KEY    | `AKIAxxx...` (AKIA prefix preserved) |
| SECRET_GITHUB_PAT | `ghp_xxx...`                         |
| SECRET_JWT        | same segment lengths, random base64  |
| IP_ADDRESS        | valid random IPv4                    |


---

## Requirements

- macOS (uses launchd for auto-start; the proxy itself runs on any OS)
- Python 3.9+
- ~685 MB RAM for the spaCy NER model

---

## Quick start

### 1. Install dependencies

```bash
cd ~/path/to/pii-proxy
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m spacy download en_core_web_sm
```

> **Tip:** If spaCy is already installed system-wide (via uv or Homebrew) and the model won't load inside `venv`, download the wheel directly:
>
> ```bash
> ./venv/bin/pip install "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
> ```

### 2. Create your PII list

```bash
cp known_pii.example.yaml ~/.pii-proxy/known_pii.yaml
chmod 600 ~/.pii-proxy/known_pii.yaml
# edit with your real names, emails, phones, addresses, employer, family
```

### 3. Install the launchd service

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
```

### 4. Route your AI clients through the proxy

Add to `~/.zshrc` (or `~/.bashrc`):

```bash
export ANTHROPIC_BASE_URL=http://localhost:8082
export OPENAI_BASE_URL=http://localhost:8082
```

Restart your terminal (and any AI clients) to pick up the change.

### 5. Verify

```bash
curl -s http://localhost:8082/health | python3 -m json.tool
```

You should see `"status": "ok"` and a `map_entries` count. Send a message in Claude Code — the count will grow.

---

## `known_pii.yaml` structure

```yaml
identity:
  names:
    - Your Full Name
    - Nickname
  emails:
    - you@example.com
  phones:
    - "+1-555-000-0000"
  addresses:
    - 123 Main St, Springfield IL 62701

employer:
  names:
    - Company Name
    - ABBREV
  domains:
    - company.com

family:
  - names: ["Spouse Name", "Spouse"]
    relationship: spouse
  - names: ["Child Name"]
    relationship: child

projects:
  - codename: InternalName
    real_name: ExternalBrandName

ignore:
  - 8082        # port number — not sensitive
  - 127.0.0.1   # localhost — not sensitive
  # - v2.1.3   # version string the IP regex catches incorrectly
```

**Tips:**

- List every alias you go by — Stage 1 is exact-match only.
- Single words (e.g. a first name alone) won't be caught by NER (requires ≥2 words), so add them explicitly here.
- Use `ignore:` for values the pipeline flags incorrectly (port numbers, internal IPs, version strings).
- Changes take effect on proxy restart.

---

## Managing the proxy

```bash
# Status and map entry count
curl -s http://localhost:8082/health

# View the full real→fake map
curl -s http://localhost:8082/map | python3 -m json.tool

# Restart (picks up changes to pii_proxy.py or known_pii.yaml)
launchctl kickstart -k gui/$(id -u)/com.jai.pii-proxy

# Stop
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist

# Start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist

# Reset the pseudonym map (all fakes regenerate on next request)
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
rm ~/.pii-proxy/map.json
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
```

---

## Debugging

### Live redaction log

```bash
tail -f /tmp/pii-proxy.err
```

Labels in the log correspond to which part of the request body triggered the redaction:

```
[system]      — system prompt
[user]        — latest user message (full NER); history user messages (map replay)
[assistant]   — prior assistant turns
[tool]        — OpenAI tool role (map replay)
[tool_result] — Anthropic tool_result blocks
```

Example:

```
2026-05-17 08:29:30 INFO   [system] redacted: 'you@company.com' → 'john85@example.org'
2026-05-17 08:29:30 INFO   [user] redacted: 'Your Name' → 'Grace Daniels'
```

### Why are old messages being redacted on every request?

Claude Code sends the **full conversation history** in every API call. The proxy scans all of it — not just your latest message. History messages use fast map replay rather than spaCy NER, so the cost stays flat regardless of conversation length.

### Run tests

```bash
cd ~/path/to/pii-proxy
./venv/bin/python tests/test_roundtrip.py
```

### Test a specific string manually

```python
./venv/bin/python - <<'EOF'
from anonymizer import anonymize_text, load_nlp, load_known_pii
from session_map import SessionMap

nlp = load_nlp()
smap = SessionMap(path=None)
known_pii = load_known_pii("/Users/you/.pii-proxy/known_pii.yaml")

text = "My name is Your Name, email is you@company.com"
anon, rep = anonymize_text(text, nlp, smap, known_pii)
print("Anonymized:", anon)
print("Restored:", smap.deanonymize(anon))
EOF
```

---

## PDF handling

By default, PDF blocks pass through unmodified — Anthropic's servers decode them server-side.

To enable PDF text extraction and PII scanning, set `PII_PDF_SCAN=true` and install pymupdf:

```bash
./venv/bin/pip install "pymupdf>=1.24"
export PII_PDF_SCAN=true
```

For a permanent setting, add `PII_PDF_SCAN` to the `EnvironmentVariables` dict in your launchd plist, then reload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
```

When enabled, each `type: document` PDF block is extracted with pymupdf, the full detection pipeline runs on the text, and the block is replaced with pseudonymized plain text before forwarding.

**Tradeoffs:**


|                               | PDF_SCAN off       | PDF_SCAN on                      |
| ----------------------------- | ------------------ | -------------------------------- |
| PII in PDFs redacted          | No                 | Yes                              |
| Claude sees PDF formatting    | Yes                | No — plain text only             |
| Claude sees images in the PDF | Yes                | No — images are discarded        |
| Scanned PDFs (image-based)    | Readable by Claude | Blank — no text layer to extract |
| Processing overhead           | None               | ~5–20ms per page                 |


Best for: text-heavy documents where layout is not critical (contracts, reports, HR documents). Leave disabled when Claude needs to reason about visual layout, forms, or embedded images.

---

## Performance


| Component             | Cost           | Scales with                             |
| --------------------- | -------------- | --------------------------------------- |
| spaCy NER             | 5–50ms         | fixed per request (latest message only) |
| Regex + secret scan   | <1ms           | message size                            |
| Map replay (history)  | <1ms           | session map size × history length       |
| Streaming deanonymize | <1ms per chunk | chunk size                              |
| Localhost loopback    | <1ms           | —                                       |
| spaCy model in RAM    | ~685MB fixed   | —                                       |


The dominant latency is always the upstream API (1–30+ seconds). Proxy overhead is well under 100ms.

---

## Common issues


| Symptom                                  | Cause                                    | Fix                                                                                 |
| ---------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| `curl health` returns connection refused | Proxy not running                        | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist`   |
| spaCy model not found at startup         | Model installed to wrong environment     | Run `./venv/bin/python -m spacy download en_core_web_sm`                            |
| Real name not redacted                   | Single-word name not in `known_pii.yaml` | NER requires ≥2 words; add the name explicitly to the YAML                          |
| PII appears in Claude's response         | Tool input not deanonymized              | Streaming tool inputs are deanonymized; check logs for missing label                |
| Map grows without bound                  | Each unique real value gets one entry    | Expected; entries are tiny (~100 bytes each)                                        |
| Fakes changed after map delete           | Map deleted without proxy restart        | Stop proxy → delete map → start proxy; never delete while running                   |
| `ANTHROPIC_BASE_URL` not picked up       | Env var set after Claude Code launched   | Restart Claude Code after setting the env var                                       |
| OpenAI requests not redacted             | Using wrong path                         | Confirm client sends to `/v1/chat/completions`; other paths pass through unmodified |


---

## Security notes

- `~/.pii-proxy/` is mode `0700`; `map.json` and `known_pii.yaml` are mode `0600`.
- The `/map` endpoint binds to `127.0.0.1` only — not reachable from the network.
- Deny rules in `~/.claude/settings.json` block Claude from reading `~/.pii-proxy/**` directly.
- Secrets (AWS keys, tokens, etc.) are pseudonymized, not erased. The proxy holds the real value in memory and in `map.json`; the upstream API only ever sees the fake. De-anonymization restores real values so model-generated tool calls (e.g. writing a `.env` file) contain correct credentials on your disk.

---

## Disabling the proxy

To run Claude Code without anonymization, you need to both unset the env var and relaunch Claude Code (it inherits env vars at startup, not dynamically).

**Temporarily (current terminal session only):**

```bash
unset ANTHROPIC_BASE_URL
unset OPENAI_BASE_URL
# relaunch Claude Code from this terminal
```

The proxy can stay running — Claude Code just won't route through it.

**Permanently (until you re-enable):**

Comment out the lines in `~/.zshrc`:

```bash
# export ANTHROPIC_BASE_URL=http://localhost:8082
# export OPENAI_BASE_URL=http://localhost:8082
```

Open a new terminal and relaunch Claude Code.

**To also stop the proxy process:**

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
```

**To re-enable:**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist
# uncomment ANTHROPIC_BASE_URL/OPENAI_BASE_URL in ~/.zshrc, then restart terminal + Claude Code
```

The env var is the real switch — the proxy can be running but harmless as long as Claude Code doesn't point at it.

---

## Contributing

Issues and pull requests are welcome. Before submitting a change:

1. Run the test suite: `./venv/bin/python tests/test_roundtrip.py`
2. Keep new detection patterns in `secret_scan.py` or `anonymizer.py` as appropriate
3. Add a test case in `tests/test_roundtrip.py` for any new PII type or edge case

---

## License

Apache 2.0 — see [LICENSE](LICENSE).