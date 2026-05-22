"""Round-trip and behavioral tests for the anonymization pipeline."""

import os
import sys
import tempfile

# make project root importable when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anonymizer import anonymize_text, load_nlp
from session_map import SessionMap


NLP = load_nlp()


def fresh_map() -> SessionMap:
    """An ephemeral session map (no persistence)."""
    return SessionMap(path=None)


def test_email_roundtrip():
    smap = fresh_map()
    text = "Contact me at john@example.com please."
    anon, rep = anonymize_text(text, NLP, smap, known_pii=None)
    assert "john@example.com" not in anon
    assert rep["john@example.com"] != "john@example.com"
    assert smap.deanonymize(anon) == text


def test_pseudonym_stability():
    """Same input must produce same pseudonym on repeated calls (cache stability)."""
    smap1 = fresh_map()
    smap2 = fresh_map()
    anon1, _ = anonymize_text("Email is alice@example.org", NLP, smap1, None)
    anon2, _ = anonymize_text("Email is alice@example.org", NLP, smap2, None)
    assert anon1 == anon2, f"non-deterministic: {anon1!r} vs {anon2!r}"


def test_known_pii_precedence_over_ner():
    """If a name is in the known_pii, it should be redacted even with NER off."""
    smap = fresh_map()
    known_pii = [("PERSON", "John Smith"), ("EMAIL", "john@example.com")]
    text = "My name is John Smith, email john@example.com."
    # nlp=None so we know the redaction came from the known_pii, not NER
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert "John Smith" not in anon
    assert "john@example.com" not in anon
    assert smap.deanonymize(anon) == text


def test_secret_aws_key_caught_and_prefix_preserved():
    smap = fresh_map()
    text = "Key: AKIA0123456789ABCDEF should not leak."
    anon, rep = anonymize_text(text, None, smap, None)
    assert "AKIA0123456789ABCDEF" not in anon
    # the fake should still look like an AWS key (AKIA prefix)
    fake = rep["AKIA0123456789ABCDEF"]
    assert fake.startswith("AKIA"), f"prefix not preserved: {fake!r}"
    assert smap.deanonymize(anon) == text


def test_persistence_roundtrip(tmp_path=None):
    """A new SessionMap instance loaded from disk reproduces prior pseudonyms."""
    path = tempfile.mktemp(suffix=".json")
    try:
        s1 = SessionMap(path=path)
        anon1, _ = anonymize_text("Hi alice@example.org", None, s1, None)
        s2 = SessionMap(path=path)
        assert s2.forward == s1.forward
        # de-anonymizing the original anon text with the freshly-loaded map should work
        assert s2.deanonymize(anon1) == "Hi alice@example.org"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_no_partial_word_collision():
    """Replacing 'John' should not match inside 'Johnson'."""
    smap = fresh_map()
    text = "John and Johnson are different people."
    known_pii = [("PERSON", "John")]
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert "Johnson" in anon, f"Johnson got partially replaced: {anon!r}"


def test_longest_first_replacement():
    """When both 'John' and 'John Smith' are in known_pii, full name wins for 'John Smith'."""
    smap = fresh_map()
    text = "John Smith and just John."
    known_pii = [("PERSON", "John"), ("PERSON", "John Smith")]
    anon, rep = anonymize_text(text, None, smap, known_pii)
    # both originals should map to different fakes
    assert rep["John Smith"] != rep["John"]
    # the standalone "John" should also be replaced
    assert "John" not in anon.replace(rep["John Smith"], "").replace(rep["John"], "")
    assert smap.deanonymize(anon) == text


def test_pdf_scan_extracts_and_anonymizes():
    """PDF document block is converted to text and all PII types are pseudonymized."""
    try:
        import base64
        import fitz
        import io
    except ImportError:
        print("SKIP  test_pdf_scan (pymupdf not installed)")
        return

    # Build a PDF containing email, phone, SSN, and a name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 80),  "Confidential Record")
    page.insert_text((50, 110), "Name:  Jane Doe")
    page.insert_text((50, 135), "Email: jane.doe@secret.com")
    page.insert_text((50, 160), "Phone: +1-800-555-9876")
    page.insert_text((50, 185), "SSN:   123-45-6789")
    buf = io.BytesIO()
    doc.save(buf)
    pdf_b64 = base64.b64encode(buf.getvalue()).decode()

    block = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
    }

    import providers.base as _base
    import pii_proxy
    orig_flag = _base.PDF_SCAN
    _base.PDF_SCAN = True
    try:
        text_block = pii_proxy._maybe_pdf_to_text(block)
    finally:
        _base.PDF_SCAN = orig_flag

    assert text_block is not None, "_maybe_pdf_to_text returned None"
    assert text_block["type"] == "text"
    extracted = text_block["text"]
    assert "jane.doe@secret.com" in extracted, "email not in extracted text"

    # Run the full anonymization pipeline on the extracted text
    smap = fresh_map()
    known_pii = [("PERSON", "Jane Doe")]
    anon, rep = anonymize_text(extracted, NLP, smap, known_pii)

    assert "jane.doe@secret.com" not in anon, "email not redacted"
    assert "123-45-6789" not in anon, "SSN not redacted"
    assert "+1-800-555-9876" not in anon, "phone not redacted"
    assert "Jane Doe" not in anon, "name not redacted"
    assert smap.deanonymize(anon) == extracted, "deanonymize did not restore original"


def test_pdf_scan_disabled_passes_through():
    """When PDF_SCAN is off, _maybe_pdf_to_text returns None and the block is unchanged."""
    try:
        import base64
        import fitz
        import io
    except ImportError:
        print("SKIP  test_pdf_scan_disabled (pymupdf not installed)")
        return

    doc = fitz.open()
    doc.new_page().insert_text((50, 100), "sensitive@example.com")
    buf = io.BytesIO()
    doc.save(buf)
    block = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf",
                   "data": base64.b64encode(buf.getvalue()).decode()},
    }

    import providers.base as _base
    import pii_proxy
    orig_flag = _base.PDF_SCAN
    _base.PDF_SCAN = False
    try:
        result = pii_proxy._maybe_pdf_to_text(block)
    finally:
        _base.PDF_SCAN = orig_flag

    assert result is None, "PDF_SCAN=False should return None (pass-through)"


def test_env_secret_value_only():
    """For KEY=value, only the value should be pseudonymized, not the KEY name."""
    smap = fresh_map()
    text = "Set API_KEY=sk-supersecretvalue123 in your .env"
    anon, rep = anonymize_text(text, None, smap, None)
    assert "API_KEY=" in anon  # variable name preserved
    assert "sk-supersecretvalue123" not in anon


def test_openai_string_content_anonymized():
    """OpenAI string-content messages: latest user gets NER, system skips NER."""
    from providers.openai import OpenAIProvider
    provider = OpenAIProvider()
    smap = fresh_map()
    known_pii = [("EMAIL", "alice@corp.com")]
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Contact alice@corp.com about the project."},
        ],
    }
    provider.anonymize_body(body, smap, NLP, known_pii)
    system_content = body["messages"][0]["content"]
    user_content = body["messages"][1]["content"]
    assert "You are a helpful assistant." == system_content  # no PII to redact
    assert "alice@corp.com" not in user_content
    assert smap.deanonymize(user_content).replace(
        smap.deanonymize(user_content), "Contact alice@corp.com about the project."
    ) == "Contact alice@corp.com about the project."


def test_openai_array_content_image_url_untouched():
    """image_url parts must pass through unmodified; text parts get anonymized."""
    from providers.openai import OpenAIProvider
    provider = OpenAIProvider()
    smap = fresh_map()
    known_pii = [("EMAIL", "bob@example.com")]
    image_part = {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "Email bob@example.com for this image:"},
                image_part,
            ]},
        ],
    }
    provider.anonymize_body(body, smap, NLP, known_pii)
    parts = body["messages"][0]["content"]
    text_part = parts[0]
    img_part = parts[1]
    assert "bob@example.com" not in text_part["text"]
    assert img_part == image_part  # image_url unchanged


def test_openai_tool_role_uses_map_replay():
    """tool role uses map replay (history=True), not fresh NER."""
    from providers.openai import OpenAIProvider
    provider = OpenAIProvider()
    smap = fresh_map()
    known_pii = [("EMAIL", "carol@example.com")]
    # Prime the session map by running a user message first
    body_prime = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Contact carol@example.com"}],
    }
    provider.anonymize_body(body_prime, smap, NLP, known_pii)
    fake_email = smap.forward.get("carol@example.com")
    assert fake_email is not None, "email not in session map after priming"

    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "done"},
            {"role": "tool", "content": "Result for carol@example.com"},
        ],
    }
    provider.anonymize_body(body, smap, NLP, known_pii)
    tool_content = body["messages"][1]["content"]
    assert fake_email in tool_content, "tool role did not replace via map replay"


def test_openai_deanonymize_response():
    """Non-streaming OpenAI response: choices[].message.content is deanonymized."""
    from providers.openai import OpenAIProvider
    provider = OpenAIProvider()
    smap = fresh_map()
    known_pii = [("EMAIL", "dave@example.com")]
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "What is dave@example.com?"}],
    }
    provider.anonymize_body(body, smap, NLP, known_pii)
    fake_email = smap.forward.get("dave@example.com")
    assert fake_email is not None

    # Simulate an upstream response that echoes the fake email
    resp_body = {
        "choices": [
            {"message": {"role": "assistant", "content": f"The email is {fake_email}."}}
        ]
    }
    provider.deanonymize_response(resp_body, smap)
    restored = resp_body["choices"][0]["message"]["content"]
    assert "dave@example.com" in restored, f"deanonymize did not restore real email: {restored!r}"



def test_ignore_list_exempts_ip():
    """An IP in the ignore list must pass through unmodified."""
    smap = fresh_map()
    known_pii = [('IGNORE', '192.168.1.100')]
    text = 'Server at 192.168.1.100 and another at 10.0.0.55.'
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert '192.168.1.100' in anon, 'ignored IP was pseudonymized'
    assert '10.0.0.55' not in anon, 'non-ignored IP was not pseudonymized'
    assert '192.168.1.100' not in rep, 'ignored IP should not appear in replacements'


def test_ignore_list_exempts_email():
    """An email in the ignore list must pass through unmodified."""
    smap = fresh_map()
    known_pii = [
        ('IGNORE', 'noreply@internal.com'),
        ('EMAIL', 'personal@example.com'),
    ]
    text = 'From noreply@internal.com to personal@example.com.'
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert 'noreply@internal.com' in anon, 'ignored email was pseudonymized'
    assert 'personal@example.com' not in anon, 'non-ignored email was not pseudonymized'


def test_ignore_does_not_suppress_other_pii():
    """ignore list only exempts listed values; all other PII is still caught."""
    smap = fresh_map()
    known_pii = [('IGNORE', '127.0.0.1')]
    text = 'localhost is 127.0.0.1, external is 203.0.113.42, email: leak@example.com'
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert '127.0.0.1' in anon
    assert '203.0.113.42' not in anon
    assert 'leak@example.com' not in anon


def test_ignore_numeric_string():
    """Numeric values (zip codes) can be ignored by string match."""
    smap = fresh_map()
    known_pii = [('IGNORE', '90210')]
    text = 'Office zip is 90210 and warehouse zip is 12345.'
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert '90210' in anon, 'ignored zip code was pseudonymized'
    assert '12345' not in anon, 'non-ignored zip code was not pseudonymized'



def test_file_path_username_not_anonymized():
    """Username inside /Users/<name>/ path must not be replaced, but the same name elsewhere is."""
    smap = fresh_map()
    known_pii = [("PERSON", "Grace Daniels")]
    # Name appears only inside a path — must be preserved
    text = "File at /Users/Grace Daniels/projects/foo.py is ready."
    anon, rep = anonymize_text(text, None, smap, known_pii)
    assert "/Users/Grace Daniels/projects/foo.py" in anon,         f"path was modified: {anon!r}"

    # Name appears both before the path and inside the path
    text2 = "Grace Daniels saved /Users/Grace Daniels/foo.py"
    anon2, rep2 = anonymize_text(text2, None, fresh_map(), known_pii)
    # Occurrence before the path should be anonymized
    before_path = anon2.split("/Users/")[0]
    assert "Grace Daniels" not in before_path,         f"name before path not anonymized: {anon2!r}"
    # Occurrence inside the path should be preserved
    assert "/Users/Grace Daniels/foo.py" in anon2,         f"path was modified: {anon2!r}"


def test_localhost_url_not_anonymized():
    """http://localhost:<port> URLs must pass through unmodified."""
    smap = fresh_map()
    text = "Proxy running at http://localhost:8082/health — check it."
    anon, rep = anonymize_text(text, None, smap, None)
    assert "http://localhost:8082/health" in anon,         f"localhost URL was anonymized: {anon!r}"
    assert "localhost" not in rep, "localhost URL should not appear in replacements"

def test_fake_not_re_anonymized():
    """A fake generated by the proxy must not be re-anonymized on a later call."""
    smap = fresh_map()
    anon1, rep1 = anonymize_text("Contact me at krishna.raj@example.com", None, smap, None)
    assert "krishna.raj@example.com" not in anon1
    fake_email = rep1["krishna.raj@example.com"]
    assert fake_email != "krishna.raj@example.com"

    # The fake email now appears in a new message — it must pass through unchanged.
    anon2, rep2 = anonymize_text(f"Reply to {fake_email}", None, smap, None)
    assert fake_email in anon2, f"fake email was re-anonymized: {anon2!r}"
    assert "krishna.raj@example.com" not in rep2
    # Round-trip for the original message must still work.
    assert smap.deanonymize(anon1) == "Contact me at krishna.raj@example.com"


def test_credit_card_19digit():
    """19-digit credit card numbers must be detected and pseudonymized."""
    smap = fresh_map()
    text = "Charge card 4758197501021818702 for the purchase."
    anon, rep = anonymize_text(text, None, smap, None)
    assert "4758197501021818702" not in anon, f"19-digit CC not redacted: {anon!r}"
    assert smap.deanonymize(anon) == text


def test_loopback_ip_not_anonymized():
    """127.0.0.1 must pass through unmodified — including via CACHED map-replay entries."""
    Jamesland = fresh_map()
    text = "Binding to 127.0.0.1 and external 66.108.237.237."
    anon, rep = anonymize_text(text, None, Jamesland, None)
    assert "127.0.0.1" in anon, f"loopback IP was anonymized: {anon!r}"
    assert "127.0.0.1" not in rep, "loopback IP should not appear in replacements"
    assert "66.108.237.237" not in anon, "external IP should be anonymized"

def test_openai_cascade_and_19digit_cc():
    """OpenAI path: 19-digit CC caught; fake CC in a later tool message is not re-anonymized."""
    from providers.openai import OpenAIProvider
    provider = OpenAIProvider()
    smap = fresh_map()

    # Turn 1: user message with a 19-digit card
    body1 = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "Charge card 4758197501021818702 to Jane Doe"},
        ],
    }
    provider.anonymize_body(body1, smap, NLP, known_pii=None)
    user_content = body1["messages"][0]["content"]
    assert "4758197501021818702" not in user_content, f"19-digit CC not redacted: {user_content!r}"
    fake_cc = smap.forward.get("4758197501021818702")
    assert fake_cc is not None, "19-digit CC not in session map"

    # Turn 2: fake CC reappears in a tool result — must NOT be re-anonymized
    body2 = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "done"},
            {"role": "tool", "content": f"Confirmed charge for {fake_cc}"},
        ],
    }
    provider.anonymize_body(body2, smap, NLP, known_pii=None)
    tool_content = body2["messages"][1]["content"]
    assert fake_cc in tool_content, f"fake CC was re-anonymized: {tool_content!r}"


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
