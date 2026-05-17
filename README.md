# pii-proxy

A local reverse proxy that sits between Claude Code and `api.anthropic.com`. It intercepts every outgoing request, replaces personal information and credentials with realistic Faker-generated pseudonyms, then restores the real values in Claude's responses before they reach the screen. Anthropic never sees your real PII.

---

## How it works

```
Claude Code
    │  ANTHROPIC_BASE_URL=http://127.0.0.1:8082
    ▼
pii_proxy.py  (aiohttp, port 8082)
    │
    ├─ anonymize request body  ──────────────────────────────────────────
    │      [system prompt]      regex + known_pii                no NER
    │      [latest user msg]    regex + known_pii + NER          full pipeline
    │      [history user msgs]  regex + known_pii + map replay   no NER (fast)
    │      [assistant turns]    regex + known_pii                no NER
    │      [tool_result]        regex + known_pii + map replay   no NER
    │
    ├─ forward to api.anthropic.com  (pseudonymized request)
    │
    ├─ receive response
    │
    └─ deanonymize response  →  Claude Code sees real values
```

### Detection pipeline (per text block)

```
Stage 1  known_pii.yaml   exact match (highest precision, zero false positives)
Stage 2a PATTERNS regex   email, phone, SSN, credit card, IP, ZIP, URL
Stage 2b secret_scan      AWS keys, GitHub tokens, Slack tokens, JWT, private keys,
                          Stripe/OpenAI/Anthropic keys, ENV-style KEY=value secrets
Stage 3  spaCy NER        PERSON (≥2 words), GPE, LOC  — latest user message only
Stage 3' map replay       fast string-match against session map  — history messages
```

First match wins — `known_pii > regex > NER` for the same string. Replacements are applied longest-first to prevent partial matches (e.g. "John" never clobbers "Johnson").

**NER scoping:** spaCy only runs on the newest user message. All prior user messages and tool results use a fast string-match against `session_map.forward` instead — anything NER ever discovered is already stored there, so no coverage is lost and NER cost stays constant regardless of conversation length.

### Pseudonymization

`fake_for(label, original)` seeds Faker with `md5(original)[:8]` so the same real value always produces the same fake. This keeps Anthropic's prompt cache warm and makes Claude's reasoning consistent across turns.

| Label | Fake looks like |
|---|---|
| PERSON | `Grace Daniels` |
| EMAIL | `johnsonkenneth@example.com` |
| PHONE | `+1-800-555-0199` |
| ADDRESS | `USS Steele, FPO AE 36325` |
| EMPLOYER / ORG | `Steele, Bond and Huff` |
| SECRET_AWS_KEY | `AKIAxxx...` (AKIA prefix preserved) |
| SECRET_GITHUB_PAT | `ghp_xxx...` |
| SECRET_JWT | same segment lengths, random base64 |
| IP_ADDRESS | valid random IPv4 |

---

## File map

### Project source

```
pii-proxy/
├── pii_proxy.py          main proxy — request routing, anonymize/deanonymize, /health, /map
├── anonymizer.py         tiered detection pipeline, NER config, known_pii loader
├── pseudonymizer.py      deterministic Faker generator (fake_for)
├── secret_scan.py        credential regex patterns (SECRET_* labels)
├── session_map.py        persistent original→fake map with file locking
├── config.py             port, paths, log level
├── requirements.txt      pip dependencies
├── known_pii.example.yaml  template for your PII list
└── tests/
    └── test_roundtrip.py   8 behavioral tests (run without a live proxy)
```

### Runtime files (outside project, protected from Claude)

```
~/.pii-proxy/
├── known_pii.yaml        your real PII list (edit this to add names, emails, etc.)
└── map.json              persisted real→fake map (auto-created, mode 0600)

~/Library/LaunchAgents/
└── com.jai.pii-proxy.plist   launchd service definition

/tmp/
├── pii-proxy.log         stdout (aiohttp access log)
└── pii-proxy.err         stderr (redaction log — the one to watch)
```

### Claude Code settings

```
~/.claude/settings.json   deny rules that block Claude from reading ~/.pii-proxy/**
```

---

## Setup

### 1. Install dependencies

```bash
cd ~/path/to/pii-proxy
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
```

> If spaCy is already installed via uv/brew and the model won't load, install the wheel directly into `venv/` with the command above — do not use `spacy download` with a uv-managed environment.

### 2. Create your PII list

```bash
cp known_pii.example.yaml ~/.pii-proxy/known_pii.yaml
chmod 600 ~/.pii-proxy/known_pii.yaml
# edit with your real names, emails, phones, addresses, family, employer
```

### 3. Route Claude Code through the proxy

Add to `~/.zshrc` (or `~/.bashrc`):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8082
```

### 4. Install the launchd service (auto-start on login)

```bash
launchctl load ~/Library/LaunchAgents/com.jai.pii-proxy.plist
```

---

## `known_pii.yaml` structure

```yaml
identity:
  names:
    - Your Full Name
    - Nickname
    - Initials
  emails:
    - you@personal.com
    - you@work.com
  phones:
    - "+1-555-555-1234"
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
```

- List every alias you go by — the proxy only catches exact matches in Stage 1.
- Single words (e.g. a first name alone) won't be caught by NER (requires ≥2 words), so list them explicitly here.
- Changes take effect on proxy restart.

---

## Managing the proxy

```bash
# Status
curl -s http://localhost:8082/health

# View the full real→fake map (JSON)
curl -s http://localhost:8082/map | python3 -m json.tool

# Restart (picks up changes to pii_proxy.py or known_pii.yaml)
launchctl kickstart -k gui/$(id -u)/com.jai.pii-proxy

# Stop
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist

# Start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist

# Reset the pseudonym map (all fakes regenerate on next request)
# Stop first, delete map, then start
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

Each redaction line shows which section of the request body it came from:

```
[system]      — Claude Code's system prompt (regex + known_pii only)
[user]        — latest user message gets full NER; history user messages get map replay
[assistant]   — prior assistant turns (regex + known_pii only)
[tool_result] — Read/Bash/etc. outputs (regex + known_pii + map replay)
```

Example output:
```
2026-05-17 08:29:30 INFO   [system] redacted: 'you@email.com' → 'fake@example.net'
2026-05-17 08:29:30 INFO   [user] redacted: 'Your Name' → 'Karen Jefferson'
2026-05-17 08:29:30 INFO   [user] redacted: 'Your Name' → 'Karen Jefferson'
2026-05-17 08:29:30 INFO   [assistant] redacted: 'you@email.com' → 'fake@example.net'
```

The first `[user]` line is the latest message (NER ran). The second is a history message (map replay — no spaCy cost).

### Why are old messages being redacted on every request?

Claude Code sends the **full conversation history** in every API call. The proxy scans all of it — not just your latest message. If your real name appeared 10 turns ago, it gets caught again on turn 11. This is correct behavior.

History messages use map replay (cheap string search) rather than spaCy NER, so the cost of scanning history stays flat regardless of conversation length.

### Run tests (no live proxy needed)

```bash
cd ~/path/to/pii-proxy
./venv/bin/python tests/test_roundtrip.py
```

### Check what's in the pseudonym map

```bash
curl -s http://localhost:8082/map | python3 -m json.tool
# or read the file directly
python3 -m json.tool ~/.pii-proxy/map.json
```

### Verify the proxy is intercepting traffic

```bash
# Should show map_entries growing after you use Claude Code
watch -n2 'curl -s http://localhost:8082/health'
```

### Test a specific string manually

```python
# from the project directory
./venv/bin/python - <<'EOF'
from anonymizer import anonymize_text, load_nlp, load_known_pii
from session_map import SessionMap

nlp = load_nlp()
smap = SessionMap(path=None)
known_pii = load_known_pii("/Users/you/.pii-proxy/known_pii.yaml")

text = "My name is Your Name, email is you@email.com"
anon, rep = anonymize_text(text, nlp, smap, known_pii)
print("Anonymized:", anon)
print("Map:", rep)
print("Restored:", smap.deanonymize(anon))
EOF
```

---

## PDF handling

### Default behaviour (PDF_SCAN disabled)

Claude Code sends PDFs to the API as base64-encoded `type: document` blocks. The proxy forwards these **unmodified** — the binary content passes straight through and Anthropic's servers decode it server-side. No PII scanning occurs on PDF content.

### Enabling PDF scanning (opt-in)

Set the environment variable before starting the proxy:

```bash
export PII_PDF_SCAN=true
```

Or add it to the launchd plist under `EnvironmentVariables`.

Also install the required dependency:

```bash
./venv/bin/pip install "pymupdf>=1.24"
```

When enabled, the proxy intercepts every `type: document` PDF block, extracts the text using pymupdf, runs the full detection pipeline on it, and **replaces the document block with a plain-text block** containing the pseudonymized content. Claude never sees the original PDF bytes.

### What PDF_SCAN catches

The full pipeline runs on extracted PDF text — same as a user message:

| PII type | Caught? |
|---|---|
| Email addresses | Yes — regex |
| Phone numbers | Yes — regex |
| SSN, credit cards | Yes — regex |
| API keys, tokens, secrets | Yes — secret scan |
| Names from `known_pii.yaml` | Yes — exact match |
| Previously seen names (NER-discovered) | Yes — map replay |
| Unknown names/places not in map | Yes — NER (applied as latest-message scope) |

### Tradeoffs with PDF_SCAN enabled

| | PDF_SCAN off | PDF_SCAN on |
|---|---|---|
| PII in PDFs redacted | No | Yes |
| Claude sees PDF formatting | Yes | No — plain text only |
| Claude sees images in the PDF | Yes | No — images are discarded |
| Tables / columns | Preserved | May be mangled (text extraction order varies) |
| Scanned PDFs (image-based) | Readable by Claude | Blank — no text layer to extract |
| Multi-column layouts | Preserved | May read in wrong order |
| Processing overhead | None | pymupdf extraction (~5–20ms per page) |

### Gaps even with PDF_SCAN enabled

- **Scanned / image-only PDFs** (e.g. a photographed document saved as PDF): no text layer exists, extraction returns empty, document is dropped. Use an OCR step outside the proxy if needed.
- **Embedded images inside PDFs**: photos, diagrams, and image-based tables within an otherwise text PDF are silently discarded.
- **Handwritten content**: not extractable via text layer.
- **PII in PDF metadata** (author, title fields): not currently scanned.

### Recommendation

Enable PDF_SCAN for text-heavy documents where layout is not critical — contracts, reports, email threads saved as PDF, HR documents. Leave it disabled when Claude needs to reason about visual layout, forms, or embedded images.

---

## Performance

| Component | Cost | Scales with |
|---|---|---|
| spaCy NER | 5–50ms | fixed per request (latest message only) |
| Regex + secret scan | <1ms | message size |
| Map replay (history) | <1ms | session map size × history length |
| Streaming deanonymize | <1ms per chunk | chunk size |
| Localhost loopback | <1ms | — |
| spaCy model in RAM | ~685MB fixed | — |

spaCy used to run on every user message in the full conversation history, making NER cost grow linearly with conversation length. Now NER runs only on the latest user message; history is covered by map replay (Python `str.__contains__` in C — negligible). A 100-turn session costs the same NER time as a 1-turn session.

The dominant latency is always Anthropic's own response time (1–30+ seconds). Proxy overhead is well under 100ms for typical sessions.

---

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `curl health` returns connection refused | Proxy not running | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jai.pii-proxy.plist` |
| spaCy model not found at startup | Model installed to wrong venv | Install the wheel directly into `venv/` — see Setup step 1 |
| Real name not redacted | Single-word name not in `known_pii.yaml` | NER requires ≥2 words; add the name explicitly to the YAML |
| PII appears in Claude's response | Tool input not deanonymized | Streaming tool inputs (`input_json_delta`) are deanonymized; check logs for missing label |
| Map grows without bound | Each unique real value gets one entry | This is expected; entries are tiny (~100 bytes each) |
| Fakes changed after map delete | Map deleted without proxy restart | Stop proxy → delete map → start proxy; never delete while running |
| `ANTHROPIC_BASE_URL` not picked up | Env var set after Claude Code launched | Restart Claude Code after setting the env var |

---

## Security notes

- `~/.pii-proxy/` is mode `0700`, `map.json` and `known_pii.yaml` are mode `0600`.
- The `/map` endpoint binds to `127.0.0.1` only — not reachable from the network.
- Deny rules in `~/.claude/settings.json` block Claude from reading `~/.pii-proxy/**` directly.
- Secrets (AWS keys, tokens, etc.) are pseudonymized, not erased. The proxy holds the real value in memory and in `map.json`; Anthropic only ever sees the fake. De-anonymization restores real values so Claude-generated tool calls (e.g. writing a `.env` file) contain correct credentials on your disk.
