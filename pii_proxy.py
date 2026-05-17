#!/usr/bin/env python3
"""
PII Anonymization Proxy — sits between Claude Code and api.anthropic.com.

Start:  python pii_proxy.py
Config: ANTHROPIC_BASE_URL=http://127.0.0.1:8082 (set in shell / launchd)
Health: curl http://localhost:8082/health
Map:    curl http://localhost:8082/map     (debug — lists redactions)
"""

import json
import logging

import aiohttp
from aiohttp import web

from anonymizer import anonymize_text, load_known_pii, load_nlp
from config import ANTHROPIC_BASE, KNOWN_PII_PATH, LOG_LEVEL, MAP_PATH, PORT
from session_map import SessionMap

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pii-proxy")

# Globals populated at startup.
_smap: SessionMap | None = None
_nlp = None
_known_pii: list[tuple[str, str]] = []


# ── Anonymization helpers ─────────────────────────────────────────────────────

def _anonymize_body(body: dict) -> dict:
    """Mutate body in-place; return {original: fake} for everything replaced in this request."""
    all_rep: dict[str, str] = {}

    # NER is expensive and scales with conversation length. Only run it on the
    # newest user message. History user messages use a cheap string-match against
    # the session map instead — anything NER ever discovered is already stored
    # there, so no coverage is lost.
    messages = body.get("messages", [])
    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"),
        default=-1,
    )

    def _anon(text: str, ner: bool = True, section: str = "?", history: bool = False) -> str:
        if history and _smap and _smap.forward:
            # Augment known_pii with session_map entries that appear in this text.
            # Pre-filtering with `k in text` keeps the list short before anonymize_text
            # iterates it. Label "CACHED" is fine — replacement() returns the stored
            # fake immediately for any key already in the map.
            extra = [("CACHED", k) for k in _smap.forward if k in text]
            effective_pii = list(_known_pii) + extra
        else:
            effective_pii = _known_pii
        new_text, rep = anonymize_text(text, _nlp if ner else None, _smap, effective_pii)
        for original, fake in rep.items():
            logger.info("  [%s] redacted: %r → %r", section, original, fake)
        all_rep.update(rep)
        return new_text

    # system prompt: regex + known_pii + secrets, no NER
    if isinstance(body.get("system"), str):
        body["system"] = _anon(body["system"], ner=False, section="system")
    elif isinstance(body.get("system"), list):
        for block in body["system"]:
            if block.get("type") == "text":
                block["text"] = _anon(block["text"], ner=False, section="system")

    for i, msg in enumerate(messages):
        content = msg.get("content")
        role = msg.get("role", "?")
        is_latest_user = (role == "user" and i == last_user_idx)
        is_history_user = (role == "user" and i != last_user_idx)

        if isinstance(content, str):
            msg["content"] = _anon(content, ner=is_latest_user, section=role, history=is_history_user)
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    block["text"] = _anon(
                        block["text"], ner=is_latest_user, section=role, history=is_history_user,
                    )
                elif btype == "tool_result":
                    tool_content = block.get("content", [])
                    if isinstance(tool_content, str):
                        block["content"] = _anon(tool_content, ner=False, section="tool_result", history=True)
                    else:
                        for inner in tool_content:
                            if isinstance(inner, dict) and inner.get("type") == "text":
                                inner["text"] = _anon(inner["text"], ner=False, section="tool_result", history=True)

    return all_rep


def _deanonymize_response(resp_body: dict) -> None:
    for block in resp_body.get("content", []):
        if block.get("type") == "text":
            block["text"] = _smap.deanonymize(block["text"])
        elif block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
            block["input"] = _deanonymize_obj(block["input"])


def _deanonymize_obj(obj):
    if isinstance(obj, str):
        return _smap.deanonymize(obj)
    if isinstance(obj, list):
        return [_deanonymize_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _deanonymize_obj(v) for k, v in obj.items()}
    return obj


# ── Request handlers ──────────────────────────────────────────────────────────

async def handle(request: web.Request) -> web.Response | web.StreamResponse:
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    # Non-JSON or GET: pass through untouched.
    if request.content_type != "application/json" or request.method == "GET":
        raw = await request.read()
        async with aiohttp.ClientSession() as client:
            async with client.request(
                request.method,
                f"{ANTHROPIC_BASE}{request.path_qs}",
                data=raw,
                headers=fwd_headers,
            ) as resp:
                body = await resp.read()
                return web.Response(body=body, status=resp.status,
                                    content_type=resp.content_type)

    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid JSON")

    replacements = _anonymize_body(body)

    if body.get("stream"):
        return await _handle_stream(request, body, fwd_headers, replacements)

    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{ANTHROPIC_BASE}{request.path_qs}",
            json=body,
            headers=fwd_headers,
        ) as resp:
            resp_body = await resp.json(content_type=None)
            resp_status = resp.status

    _deanonymize_response(resp_body)
    logger.info("anonymized=%d (map size=%d)", len(replacements), len(_smap.forward))
    return web.json_response(resp_body, status=resp_status)


async def _handle_stream(
    request: web.Request,
    body: dict,
    fwd_headers: dict,
    replacements: dict,
) -> web.StreamResponse:
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
    )
    await response.prepare(request)

    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{ANTHROPIC_BASE}{request.path_qs}",
            json=body,
            headers=fwd_headers,
        ) as resp:
            buf = ""
            async for chunk in resp.content.iter_chunked(4096):
                text = buf + chunk.decode("utf-8", errors="replace")
                lines = text.split("\n")
                buf = lines[-1]

                for line in lines[:-1]:
                    if line.startswith("data: ") and line != "data: [DONE]":
                        payload = line[6:]
                        try:
                            data = json.loads(payload)
                            delta = data.get("delta", {})
                            dtype = delta.get("type")
                            if dtype == "text_delta" and "text" in delta:
                                delta["text"] = _smap.deanonymize(delta["text"])
                            elif dtype == "input_json_delta" and "partial_json" in delta:
                                delta["partial_json"] = _smap.deanonymize(delta["partial_json"])
                            line = "data: " + json.dumps(data)
                        except (json.JSONDecodeError, KeyError):
                            pass
                    await response.write((line + "\n").encode())

            if buf:
                await response.write(buf.encode())

    logger.info("stream anonymized=%d (map size=%d)", len(replacements), len(_smap.forward))
    await response.write_eof()
    return response


# ── Observability endpoints ───────────────────────────────────────────────────

async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "map_entries": len(_smap.forward) if _smap else 0})


async def map_dump(request: web.Request) -> web.Response:
    """Debug endpoint — returns the full original→fake map. Local-only by binding to 127.0.0.1."""
    return web.json_response({"forward": dict(_smap.forward) if _smap else {}})


# ── App setup ─────────────────────────────────────────────────────────────────

async def _on_startup(app: web.Application) -> None:
    global _nlp, _smap, _known_pii
    logger.info("loading spaCy model…")
    _nlp = load_nlp()
    logger.info("spaCy ready: %s", _nlp.meta["name"] if _nlp else "none (regex only)")

    _known_pii = load_known_pii(KNOWN_PII_PATH)
    _smap = SessionMap(MAP_PATH)
    logger.info("PII proxy listening on 127.0.0.1:%d  (known_pii=%d, map=%d)",
                PORT, len(_known_pii), len(_smap.forward))


app = web.Application()
app.on_startup.append(_on_startup)
app.router.add_get("/health", health)
app.router.add_get("/map", map_dump)
app.router.add_route("*", "/{path_info:.*}", handle)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
