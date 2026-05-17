"""OpenAI provider — handles /v1/chat/completions."""

import json
import logging

import aiohttp
from aiohttp import web

import config
from .base import Provider

logger = logging.getLogger("pii-proxy")


class OpenAIProvider(Provider):
    base_url = config.OPENAI_BASE

    def anonymize_body(self, body: dict, smap, nlp, known_pii) -> dict:
        all_rep: dict[str, str] = {}
        messages = body.get("messages", [])
        last_user_idx = self._last_user_idx(messages)
        _anon = self._make_anon(smap, nlp, known_pii, all_rep)

        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content")

            # NER scoping rules (same as Anthropic, mapped to OpenAI roles):
            # system → no NER (regex + known_pii only)
            # latest user → full NER
            # history user → map replay
            # assistant → no NER
            # tool → map replay
            is_latest_user = (role == "user" and i == last_user_idx)
            is_history_user = (role == "user" and i != last_user_idx)
            use_ner = is_latest_user
            use_history = is_history_user or role == "tool"

            if isinstance(content, str):
                msg["content"] = _anon(content, ner=use_ner, section=role, history=use_history)
            elif isinstance(content, list):
                processed = []
                for part in content:
                    if part.get("type") == "text":
                        processed.append({**part, "text": _anon(
                            part["text"], ner=use_ner, section=role, history=use_history,
                        )})
                    else:
                        # image_url and other parts pass through untouched
                        processed.append(part)
                msg["content"] = processed

        return all_rep

    def deanonymize_response(self, resp_body: dict, smap) -> None:
        for choice in resp_body.get("choices", []):
            message = choice.get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = smap.deanonymize(content)
            # tool_calls in non-streaming response
            for tc in message.get("tool_calls", []):
                fn = tc.get("function", {})
                if "arguments" in fn:
                    fn["arguments"] = smap.deanonymize(fn["arguments"])

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
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta and delta["content"] is not None:
                                        delta["content"] = smap.deanonymize(delta["content"])
                                    for tc in delta.get("tool_calls", []):
                                        fn = tc.get("function", {})
                                        if "arguments" in fn:
                                            fn["arguments"] = smap.deanonymize(fn["arguments"])
                                line = "data: " + json.dumps(data)
                            except (json.JSONDecodeError, KeyError):
                                pass
                        await response.write((line + "\n").encode())

                if buf:
                    await response.write(buf.encode())

        logger.info("stream anonymized=%d (map size=%d)", len(replacements), len(smap.forward))
        await response.write_eof()
        return response
