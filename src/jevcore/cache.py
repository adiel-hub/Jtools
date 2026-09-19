"""Answer caches.

Identical (model, state, question) triples are judged once. An in-memory dict covers one run; the
SQLite file under ``~/.cache/jev`` makes reruns free and exact, and is shared with jgrep-compatible
tools that use the same key scheme. Both are keyed on a hash of the canonical question and state.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backends import cache_dir
from .questions import Answer, ChoiceAnswer, NoulAnswer, Question, ScoreAnswer, State, canonical, canonical_state


def cache_key(model: str, state: State, question: Question) -> str:
    blob = json.dumps([model, canonical_state(state), canonical(question)], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def encode_answer(answer: Answer) -> dict[str, Any]:
    if isinstance(answer, NoulAnswer):
        return {"type": "noul", "p": answer.probability}
    if isinstance(answer, ChoiceAnswer):
        return {
            "type": "choice",
            "choice": answer.choice,
            "probabilities": dict(answer.probabilities),
            "confidence": answer.confidence,
        }
    return {
        "type": "score",
        "score": answer.score,
        "levels": answer.levels,
        "probabilities": {str(k): v for k, v in answer.probabilities.items()},
        "confidence": answer.confidence,
        "legend": {str(k): v for k, v in answer.legend.items()},
    }


def decode_answer(data: dict[str, Any]) -> Answer | None:
    """The inverse of :func:`encode_answer`; ``None`` for anything unrecognisable (stale cache rows)."""
    try:
        kind = data["type"]
        if kind == "noul":
            return NoulAnswer(float(data["p"]))
        if kind == "choice":
            return ChoiceAnswer(
                str(data["choice"]),
                {str(k): float(v) for k, v in data["probabilities"].items()},
                data.get("confidence"),
            )
        if kind == "score":
            return ScoreAnswer(
                score=float(data["score"]),
                probabilities={int(k): float(v) for k, v in data["probabilities"].items()},
                levels=int(data["levels"]),
                confidence=data.get("confidence"),
                legend={int(k): str(v) for k, v in (data.get("legend") or {}).items()},
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


class MemoryCache:
    """One run's answers. Always on; costs nothing."""

    def __init__(self) -> None:
        self._data: dict[str, Answer] = {}

    def get(self, key: str) -> Answer | None:
        return self._data.get(key)

    def put(self, key: str, answer: Answer) -> None:
        self._data[key] = answer

    def __len__(self) -> int:
        return len(self._data)

    def close(self) -> None:
        return None


class DiskCache:
    """Answers on disk, keyed on the model asked for, the state and the question.

    Keyed on the model **asked for**, not the version that answered, because a run cannot know the
    version until a response arrives and the tools that read their input whole ask everything at
    once: keying on the resolved version means nothing ever finds its own rows again and every
    rerun re-pays for the corpus.

    That leaves the alias problem: ``jev-latest`` moves, and rows written under it would otherwise
    replay a retired version for ever. So each row records the version that produced it, and the
    alias's current meaning is recorded too. The first live response of any later run that notices
    a different meaning throws the alias's rows away and says so. A rerun with nothing new to ask
    makes no call and cannot notice, which is the one gap; ``--no-cache`` and a pinned ``--model``
    both close it.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_dir() / "answers.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        # WAL lets two tools in one pipeline share the file.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, answer TEXT NOT NULL, at REAL NOT NULL, "
            "asked TEXT, answered TEXT) WITHOUT ROWID"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS resolutions (asked TEXT PRIMARY KEY, answered TEXT NOT NULL, at REAL NOT NULL)"
        )
        for column in ("asked", "answered"):  # a file written by an earlier version has neither
            try:
                self._db.execute(f"ALTER TABLE answers ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                continue  # already there: this file was written by this version
            # The column was just added, so every existing row predates it and carries no record
            # of which model produced it. Such a row can never be reconciled against a moved
            # alias, and claiming later that it was discarded would be a lie, so it goes now.
            self._db.execute("DELETE FROM answers WHERE asked IS NULL")

    def get(self, key: str) -> Answer | None:
        with self._lock:
            row = self._db.execute("SELECT answer FROM answers WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
        except ValueError:
            return None
        return decode_answer(data) if isinstance(data, dict) else None

    def put(self, key: str, answer: Answer, asked: str = "", answered: str = "") -> None:
        payload = json.dumps(encode_answer(answer), ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?)",
                (key, payload, time.time(), asked, answered),
            )

    def reconcile(self, asked: str, answered: str) -> str | None:
        """Record what ``asked`` resolves to. Returns the previous answer when it changed, having
        dropped every row that model produced, so a moved alias cannot go on being replayed."""
        if not asked or not answered:
            return None
        with self._lock:
            row = self._db.execute("SELECT answered FROM resolutions WHERE asked = ?", (asked,)).fetchone()
            previous = row[0] if row else None
            if previous == answered:
                return None
            self._db.execute("INSERT OR REPLACE INTO resolutions VALUES (?, ?, ?)", (asked, answered, time.time()))
            if previous is None:
                return None
            # Only the answers the old version produced. Under -j 20 a sibling call may already
            # have stored an answer from the new one, and that is exactly what we want to keep.
            self._db.execute(
                "DELETE FROM answers WHERE asked = ? AND (answered IS NULL OR answered != ?)", (asked, answered)
            )
        return str(previous)

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()


class LayeredCache:
    """Memory in front of disk. Disk is optional, and may fail at any moment.

    The file is shared with other tools in the pipeline and with other versions of them, so it can
    be locked, full, on a disappearing mount, or carry a schema this build does not know. None of
    that is a reason to lose a decision the user already paid for: the disk layer is dropped, the
    run says so once, and everything continues from memory.
    """

    def __init__(self, disk: DiskCache | None = None, on_error: Callable[[str], None] | None = None) -> None:
        self.memory = MemoryCache()
        self.disk = disk
        self.on_error = on_error

    def _drop_disk(self, what: str, error: Exception) -> None:
        self.disk = None
        if self.on_error is not None:
            self.on_error(f"answer cache disabled after a {what} failure ({error}); continuing from memory")

    def get(self, key: str) -> Answer | None:
        hit = self.memory.get(key)
        if hit is not None:
            return hit
        if self.disk is not None:
            try:
                hit = self.disk.get(key)
            except (sqlite3.Error, OSError) as e:
                self._drop_disk("cache read", e)
                return None
            if hit is not None:
                self.memory.put(key, hit)
        return hit

    def put(self, key: str, answer: Answer, asked: str = "", answered: str = "") -> None:
        self.memory.put(key, answer)
        if self.disk is not None:
            try:
                self.disk.put(key, answer, asked, answered)
            except (sqlite3.Error, OSError) as e:
                self._drop_disk("cache write", e)

    def reconcile(self, asked: str, answered: str) -> str | None:
        """See :meth:`DiskCache.reconcile`. Also empties the in-memory cache when the alias moved."""
        if self.disk is None:
            return None
        try:
            previous = self.disk.reconcile(asked, answered)
        except (sqlite3.Error, OSError) as e:
            self._drop_disk("cache read", e)
            return None
        if previous is not None:
            self.memory = MemoryCache()
        return previous

    def close(self) -> None:
        if self.disk is not None:
            with contextlib.suppress(sqlite3.Error, OSError):
                self.disk.close()
