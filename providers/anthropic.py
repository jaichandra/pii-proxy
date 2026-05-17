"""Anthropic provider — handles /v1/messages."""

import json
import logging

import aiohttp
from aiohttp import web

import config
from .base import Provider

logger = logging.getLogger("pii-proxy")


class AnthropicProvider(Provider):
    base_url = config.ANTHROPIC_BASE

    def anonymize_body(self, body: dict, smap, nlp, known_pii) -> dict:
        all_rep: dict[str, str] = {}
        messages = body.get("messages", [])
        last_user_idx = self._last_user_idx(messages)
        _anon = self._make_anon(smap, nlp, known_pii, all_rep)

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
                processed = []
                for block in content:
                    pdf_block = self._maybe_pdf_to_text(block)
                    if pdf_block is not None:
                        block = pdf_block

                    btype = block.get("type")
                    if btype == "text":
                        processed.append({**block, "text": _anon(
                            block["text"], ner=is_latest_user, section=role, history=is_history_user,
                        )})
                    elif btype == "tool_result":
                        tool_content = block.get("content", [])
                        if isinstance(tool_content, str):
                            processed.append({**block, "content": _anon(
                                tool_content, ner=False, section="tool_result", history=True,
                            )})
                        else:
                            new_inner = []
                            for inner in tool_content:
                                if isinstance(inner, dict):
                                    inner = self._maybe_pdf_to_text(inner) or inner
                                    if inner.get("type") == "text":
                                        inner = {**inner, "text": _anon(
                                            inner["text"], ner=False, section="tool_result", history=True,
                                        )}
                                new_inner.append(inner)
                            processed.append({**block, "content": new_inner})
                    else:
                        processed.append(block)
                msg["content"] = processed

        return all_rep

    def deanonymize_response(self, resp_body: dict, smap) -> None:
        for block in resp_body.get("content", []):
            if block.get("type") == "text":
                block["text"] = smap.deanonymize(block["text"])
            elif block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                block["input"] = self._deanonymize_obj(block["input"], smap)

    async def handle_stream(
        self, request: web.Request, body: dict, fwd_headers: dict, replacements: dict, smap
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
        )
        await response.prepare(request)

        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.base_url}{request.path_qs}",
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
                                    delta["text"] = smap.deanonymize(delta["text"])
                                elif dtype == "input_json_delta" and "partial_json" in delta:
                                    delta["partial_json"] = smap.deanonymize(delta["partial_json"])
                                line = "data: " + json.dumps(data)
                            except (json.JSONDecodeError, KeyError):
                                pass
                        await response.write((line + "\n").encode())

                if buf:
                    await response.write(buf.encode())

        logger.info("stream anonymized=%d (map size=%d)", len(replacements), len(smap.forward))
        await response.write_eof()
        return response
