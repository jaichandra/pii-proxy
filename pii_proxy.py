#!/usr/bin/env python3
"""
PII Anonymization Proxy — sits between Claude Code / OpenAI clients and their upstream APIs.

Start:  python pii_proxy.py
Config: ANTHROPIC_BASE_URL=http://127.0.0.1:8082  (for Claude Code / Anthropic SDK)
        OPENAI_BASE_URL=http://127.0.0.1:8082      (for OpenAI SDK)
Health: curl http://localhost:8082/health
Map:    curl http://localhost:8082/map
"""

import logging

import aiohttp
from aiohttp import web

from anonymizer import load_known_pii, load_nlp
from config import ANTHROPIC_BASE, KNOWN_PII_PATH, LOG_LEVEL, MAP_PATH, PORT
from providers.anthropic import AnthropicProvider
from providers.openai import OpenAIProvider
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

_PROVIDERS = {
    "/v1/messages":         AnthropicProvider(),
    "/v1/chat/completions": OpenAIProvider(),
}


# ── Request handlers ──────────────────────────────────────────────────────────

async def handle(request: web.Request) -> web.Response | web.StreamResponse:
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    provider = _PROVIDERS.get(request.path)
    upstream = provider.base_url if provider else ANTHROPIC_BASE

    # Non-JSON, GET, or unrecognised path: pass through untouched.
    if request.content_type != "application/json" or request.method == "GET" or provider is None:
        raw = await request.read()
        async with aiohttp.ClientSession() as client:
            async with client.request(
                request.method,
                f"{upstream}{request.path_qs}",
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

    replacements = provider.anonymize_body(body, _smap, _nlp, _known_pii)

    if body.get("stream"):
        return await provider.handle_stream(request, body, fwd_headers, replacements, _smap)

    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{upstream}{request.path_qs}",
            json=body,
            headers=fwd_headers,
        ) as resp:
            resp_body = await resp.json(content_type=None)
            resp_status = resp.status

    provider.deanonymize_response(resp_body, _smap)
    logger.info("anonymized=%d (map size=%d)", len(replacements), len(_smap.forward))
    return web.json_response(resp_body, status=resp_status)


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


# Keep _maybe_pdf_to_text importable for the existing test_roundtrip.py tests that reference
# pii_proxy._maybe_pdf_to_text directly.
from providers.base import Provider as _P
_maybe_pdf_to_text = _P._maybe_pdf_to_text
PDF_SCAN = __import__("config").PDF_SCAN

app = web.Application()
app.on_startup.append(_on_startup)
app.router.add_get("/health", health)
app.router.add_get("/map", map_dump)
app.router.add_route("*", "/{path_info:.*}", handle)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
