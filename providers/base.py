"""Abstract Provider base class and shared utilities."""

import base64
import logging
from abc import ABC, abstractmethod

from config import PDF_SCAN
from anonymizer import anonymize_text

logger = logging.getLogger("pii-proxy")


class Provider(ABC):
    base_url: str

    @abstractmethod
    def anonymize_body(self, body: dict, smap, nlp, known_pii) -> dict: ...

    @abstractmethod
    def deanonymize_response(self, resp_body: dict, smap) -> None: ...

    @abstractmethod
    async def handle_stream(
        self, request, body: dict, fwd_headers: dict, replacements: dict, smap
    ): ...

    def is_streaming(self, body: dict) -> bool:
        return bool(body.get("stream"))

    # ── Shared utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _last_user_idx(messages: list) -> int:
        return max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"),
            default=-1,
        )

    @staticmethod
    def _make_anon(smap, nlp, known_pii, all_rep: dict):
        def _anon(text: str, ner: bool = True, section: str = "?", history: bool = False) -> str:
            if history and smap and smap.forward:
                extra = [("CACHED", k) for k in smap.forward if k in text]
                effective_pii = list(known_pii or []) + extra
            else:
                effective_pii = known_pii
            new_text, rep = anonymize_text(text, nlp if ner else None, smap, effective_pii)
            for original, fake in rep.items():
                logger.info("  [%s] redacted: %r → %r", section, original, fake)
            all_rep.update(rep)
            return new_text
        return _anon

    @staticmethod
    def _maybe_pdf_to_text(block: dict) -> dict | None:
        if not PDF_SCAN or block.get("type") != "document":
            return None
        source = block.get("source", {})
        if source.get("type") != "base64" or source.get("media_type") != "application/pdf":
            return None
        try:
            import fitz
        except ImportError:
            logger.warning("PDF_SCAN=true but pymupdf is not installed — pip install pymupdf")
            return None
        try:
            raw = base64.b64decode(source.get("data", ""))
            if not raw.startswith(b"%PDF"):
                return None
            with fitz.open(stream=raw, filetype="pdf") as doc:
                pages = doc.page_count
                text = "\n".join(page.get_text() for page in doc)
            logger.info("  PDF extracted: %d page(s), %d chars", pages, len(text))
            return {"type": "text", "text": text}
        except Exception as e:
            logger.warning("PDF extraction failed (%s) — passing document through unredacted", e)
            return None

    @staticmethod
    def _deanonymize_obj(obj, smap):
        if isinstance(obj, str):
            return smap.deanonymize(obj)
        if isinstance(obj, list):
            return [Provider._deanonymize_obj(x, smap) for x in obj]
        if isinstance(obj, dict):
            return {k: Provider._deanonymize_obj(v, smap) for k, v in obj.items()}
        return obj
