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


def test_env_secret_value_only():
    """For KEY=value, only the value should be pseudonymized, not the KEY name."""
    smap = fresh_map()
    text = "Set API_KEY=sk-supersecretvalue123 in your .env"
    anon, rep = anonymize_text(text, None, smap, None)
    assert "API_KEY=" in anon  # variable name preserved
    assert "sk-supersecretvalue123" not in anon


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
