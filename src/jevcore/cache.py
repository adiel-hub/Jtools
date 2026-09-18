"""Answer caches.

Identical (model, state, question) triples are judged once. An in-memory dict covers one run; the
SQLite file under ``~/.cache/jev`` makes reruns free and exact, and is shared with jgrep-compatible
tools that use the same key scheme. Both are keyed on a hash of the canonical question and state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
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
    """Answers on disk, keyed on the exact model, state and question. Safe across processes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_dir() / "answers.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        # WAL lets two tools in one pipeline share the file.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, answer TEXT NOT NULL, at REAL NOT NULL) "
            "WITHOUT ROWID"
        )

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

    def put(self, key: str, answer: Answer) -> None:
        payload = json.dumps(encode_answer(answer), ensure_ascii=False)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, payload, time.time()))

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()


class LayeredCache:
    """Memory in front of disk. Disk is optional."""

    def __init__(self, disk: DiskCache | None = None) -> None:
        self.memory = MemoryCache()
        self.disk = disk

    def get(self, key: str) -> Answer | None:
        hit = self.memory.get(key)
        if hit is not None:
            return hit
        if self.disk is not None:
            hit = self.disk.get(key)
            if hit is not None:
                self.memory.put(key, hit)
        return hit

    def put(self, key: str, answer: Answer) -> None:
        self.memory.put(key, answer)
        if self.disk is not None:
            self.disk.put(key, answer)

    def close(self) -> None:
        if self.disk is not None:
            self.disk.close()
