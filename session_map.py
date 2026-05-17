"""
Persistent original → pseudonym map, shared across all sessions.

The map is the durable record of "John Smith was replaced with Alex Johnson".
Without persistence, restarts would drop the reverse map and break
de-anonymization of any pseudonyms already in Claude Code's conversation
history. With deterministic Faker seeding, the same original always produces
the same pseudonym anyway — the persisted file is the authoritative copy and
also caches collisions we've already resolved.
"""

import fcntl
import json
import logging
import os

from pseudonymizer import fake_for

logger = logging.getLogger("pii-proxy.session_map")


class SessionMap:
    def __init__(self, path: str | None = None):
        self.forward: dict[str, str] = {}   # real → fake
        self.reverse: dict[str, str] = {}   # fake → real
        self.path = path
        if path and os.path.exists(path):
            self._load()

    def replacement(self, label: str, original: str) -> str:
        """Return the stable fake for original; generate + persist on first sight."""
        if original in self.forward:
            return self.forward[original]

        fake = fake_for(label, original)
        # collision: fake already maps to a different real value. Re-seed with
        # a salted variant until unique, fall back to a suffix.
        attempt = 0
        while fake in self.reverse and self.reverse[fake] != original:
            attempt += 1
            fake = fake_for(label, f"{original}\x00{attempt}")
            if attempt > 20:
                fake = f"{fake}{attempt}"
                break

        self.forward[original] = fake
        self.reverse[fake] = original
        self._save()
        return fake

    def preload(self, label: str, original: str) -> None:
        """Seed an entry without returning anything — used at startup for the known_pii list."""
        if original and original not in self.forward:
            self.replacement(label, original)

    def deanonymize(self, text: str) -> str:
        # longest fake first so [PERSON_10]-style longer keys aren't shadowed by [PERSON_1]
        for fake in sorted(self.reverse, key=len, reverse=True):
            if fake in text:
                text = text.replace(fake, self.reverse[fake])
        return text

    # ── persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            self.forward = dict(data.get("forward", {}))
            self.reverse = {v: k for k, v in self.forward.items()}
            logger.info("loaded %d entries from %s", len(self.forward), self.path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("could not load map at %s (%s) — starting empty", self.path, e)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump({"forward": self.forward}, f, indent=2, sort_keys=True)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            os.chmod(tmp, 0o600)
            os.rename(tmp, self.path)
        except OSError as e:
            logger.warning("could not save map to %s (%s)", self.path, e)
