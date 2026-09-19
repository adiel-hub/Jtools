"""MockJev: a deterministic stand-in for the API so the whole suite runs offline.

It speaks both wire dialects (System One and the Vercel evaluation modality) and answers from
simple, predictable rules:

- a **noul** is 0.9 when a keyword from the quoted description in the question appears in the
  state, else 0.1 (``KEYWORDS`` maps words to synonyms so tests can use natural text);
- a **choice** picks the option whose name or description shares a keyword with the state, or
  the candidate (for ``[{"id", "text"}]`` states) whose text does; otherwise the first option;
- a **score** counts keyword hits and maps them onto the rungs.

Explicit :class:`Rule` fixtures override the heuristics. The mock also scripts failures: HTTP
status sequences, a per-call delay, and a poison word that yields a 400. Use it as an
``httpx.MockTransport`` handler in-process, or :func:`serve` it over localhost for subprocess
tests (``JEV_GATEWAY_URL``).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

POISON = "POISON"
_WORD = re.compile(r"[a-z0-9]+")
_QUOTED = re.compile(r'"([^"]+)"')

# Words the tests use in descriptions -> words that count as hits in the state.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "angry": ("angry", "furious", "worst", "unacceptable", "done with", "!!", "??", "terrible", "outrage"),
    "angriest": ("angry", "furious", "worst", "unacceptable", "done with", "!!", "??", "terrible", "outrage"),
    "urgent": ("urgent", "asap", "immediately", "now", "critical", "outage"),
    "error": ("error", "fail", "failed", "exception", "traceback", "panic", "fatal"),
    "security": ("security", "breach", "leak", "cve", "attack", "unauthorized"),
    "positive": ("love", "great", "excellent", "amazing", "thanks", "happy"),
    "billing": ("invoice", "charged", "refund", "payment", "billing", "card"),
    "bug": ("crash", "crashes", "broken", "bug", "error", "fails"),
    "feature": ("add", "would love", "feature", "request", "wish", "support for"),
    "question": ("how do", "how to", "?", "can i", "where is"),
    "sales": ("pricing", "quote", "buy", "purchase", "demo", "enterprise", "seats"),
    "support": ("help", "cannot", "can't", "broken", "not working", "issue"),
    "spam": ("free", "winner", "prize", "click here", "$$$", "viagra", "casino"),
    "decision": ("decided", "we will", "agreed", "decision", "go with"),
    "hardware": ("disk", "memory", "cpu", "pci", "temperature", "ata", "nvme"),
}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _description(question: dict[str, Any]) -> str:
    instr = str(question.get("instructions", ""))
    quoted = _QUOTED.findall(instr)
    return " ".join(quoted) if quoted else instr


def _hits(description: str, text: str) -> int:
    low = text.lower()
    count = 0
    for word in _words(description):
        if len(word) < 3:
            continue
        if word in low:
            count += 1
        count += sum(1 for synonym in KEYWORDS.get(word, ()) if synonym in low)
    return count


def _state_text(state: Any) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


@dataclass(frozen=True)
class Rule:
    """An explicit fixture: when ``state_contains`` (if set) is in the state and
    ``question_contains`` (if set) is in the instructions, return ``answer`` (System One shape)."""

    answer: dict[str, Any]
    state_contains: str | None = None
    question_contains: str | None = None

    def matches(self, state_text: str, question: dict[str, Any]) -> bool:
        if self.state_contains is not None and self.state_contains not in state_text:
            return False
        return not (
            self.question_contains is not None and self.question_contains not in str(question.get("instructions", ""))
        )


@dataclass
class MockJev:
    rules: list[Rule] = field(default_factory=list)
    script: list[int] = field(default_factory=list)
    """HTTP statuses to return, one per call, before answering normally. 200 means answer."""
    delay: float = 0.0
    """Seconds each call takes (async sleep), for concurrency and ordering tests."""
    yes: float = 0.9
    no: float = 0.1
    fixed_tokens: int = 300
    model: str = "jev-1.13.0"
    """The version the answers say they came from; change it to act like a moved alias."""
    bodies: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    in_flight: int = 0
    peak_in_flight: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ----------------------------------------------------------- answering

    def answer(self, state: Any, qid: str, question: dict[str, Any]) -> dict[str, Any]:
        """One System One-shaped answer."""
        text = _state_text(state)
        for rule in self.rules:
            if rule.matches(text, question):
                return dict(rule.answer)
        kind = question.get("type")
        description = _description(question)
        if kind in ("noul", "boolean"):
            # juniq-style pair questions name an index into "kept".
            m = re.search(r'"kept"\[(\d+)\]', str(question.get("instructions", "")))
            if m and isinstance(state, dict):
                kept = state.get("kept") or []
                cand = str(state.get("candidate", ""))
                idx = int(m.group(1))
                same = idx < len(kept) and _similar(cand, str(kept[idx]))
                return {"type": "noul", "noul": self.yes if same else self.no}
            if 'marked ">"' in str(question.get("instructions", "")):
                text = "\n".join(line[2:] for line in text.split("\n") if line.startswith("> "))
            return {"type": "noul", "noul": self.yes if _hits(description, text) else self.no}
        if kind == "choice":
            return self._choice(state, text, question, description)
        if kind == "score":
            levels = question.get("criteria") or []
            n = max(2, len(levels))
            hits = _hits(description, text)
            # 0 hits -> lowest rung; 1 hit -> two rungs up; every further hit climbs one more.
            idx = 0 if hits == 0 else min(n - 1, 1 + hits)
            probs = {str(i): 0.0 for i in range(n)}
            probs[str(idx)] = 0.8
            probs[str(min(n - 1, idx + 1) if idx < n - 1 else idx - 1)] += 0.2
            score = sum(int(k) * v for k, v in probs.items())
            return {
                "type": "score",
                "score": round(score, 2),
                "probabilities": probs,
                "confidence": 0.8,
                "legend": {str(i): lv for i, lv in enumerate(levels)},
            }
        raise ValueError(f"unknown question type {kind!r}")

    def _choice(self, state: Any, text: str, question: dict[str, Any], description: str) -> dict[str, Any]:
        options: dict[str, str] = dict(question.get("criteria") or {})
        names = list(options)
        winner: str | None = None
        # Candidate lists: [{"id":..., "text":...}] possibly under "candidates" with a "target".
        candidates: list[dict[str, Any]] | None = None
        target: str | None = None
        if isinstance(state, list) and state and isinstance(state[0], dict) and "id" in state[0]:
            candidates = state
        elif isinstance(state, dict) and isinstance(state.get("candidates"), list):
            candidates = state["candidates"]
            target = _state_text(state.get("target", ""))
        hits_by_name: dict[str, int] = dict.fromkeys(names, 0)
        if candidates is not None:
            for cand in candidates:
                cid = str(cand.get("id"))
                if cid not in options:
                    continue
                ctext = str(cand.get("text", ""))
                hits_by_name[cid] = _overlap(target, ctext) if target is not None else _hits(description, ctext)
            if not any(hits_by_name.values()) and "none" in options:
                winner = "none"
        else:
            for name, desc in options.items():
                if name != "none":
                    hits_by_name[name] = _hits(f"{name} {desc}", text)
        if winner is None:
            best = max(names, key=lambda n: hits_by_name[n])  # the first of the best on ties
            if hits_by_name[best]:
                winner = best
            else:
                winner = next((n for n in names if n != "none"), names[0])
        # The winner takes 0.8; the rest share 0.2 in proportion to their own hits, so a
        # runner-up with some merit ranks above candidates with none.
        rest = [n for n in names if n != winner]
        weights = {n: hits_by_name[n] + 0.1 for n in rest}
        total = sum(weights.values())
        probs = {n: round(0.2 * w / total, 4) for n, w in weights.items()} if rest else {}
        probs[winner] = 0.8
        return {"type": "choice", "choice": winner, "probabilities": probs, "confidence": 0.8}

    # ------------------------------------------------------------- transport

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        """``httpx.MockTransport`` handler."""
        with self.lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            status, payload = self.handle(json.loads(request.content), dict(request.headers))
            return httpx.Response(status, json=payload)
        finally:
            with self.lock:
                self.in_flight -= 1

    def handle(self, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """Pure request -> (status, json) so the HTTP server can share it."""
        self.bodies.append(body)
        self.headers.append(headers)
        vercel = "ai-evaluation-model-specification-version" in {k.lower() for k in headers}
        if self.script:
            status = self.script.pop(0)
            if status != 200:
                return status, {"error": {"message": f"scripted {status}", "type": "scripted"}}
        state = body.get("state")
        if POISON in _state_text(state):
            return 400, {"error": {"message": "poisoned state", "type": "invalid_request_error"}}
        questions = body.get("questions") or {}
        if not questions:
            return 400, {"error": {"message": "At least one question is required"}}
        answers: dict[str, Any] = {}
        confidence: dict[str, float] = {}
        for qid, q in questions.items():
            ans = self.answer(state, qid, q)
            if vercel:
                if ans["type"] == "noul":
                    ans = {"type": "boolean", "probability": ans["noul"]}
                else:
                    conf = ans.pop("confidence", None)
                    ans.pop("legend", None)
                    if conf is not None:
                        confidence[qid] = conf
            answers[qid] = ans
        tokens = self.fixed_tokens + len(_state_text(state)) // 4
        if vercel:
            return 200, {
                "answers": answers,
                "rounding": {"probabilityDecimals": 2, "scoreDecimals": 2},
                "usage": {"inputTokens": tokens, "outputTokens": 10},
                "warnings": [],
                "providerMetadata": {
                    "typesafe": {"confidence": confidence},
                    "gateway": {
                        "cost": f"{tokens * 0.042 / 1e6:.10f}",
                        "routing": {"canonicalSlug": "typesafe-ai/jev"},
                    },
                },
            }
        return 200, {"model": self.model, "answers": answers, "usage": {"input_tokens": tokens, "output_tokens": 10}}


def _overlap(a: str | None, b: str) -> int:
    if a is None:
        return 0
    wa = {w for w in _words(a) if len(w) >= 3}
    wb = {w for w in _words(b) if len(w) >= 3}
    return len(wa & wb)


def _similar(a: str, b: str) -> bool:
    """Two lines "mean the same" for the mock when they share most of their content words."""
    wa = {w for w in _words(a) if len(w) >= 3}
    wb = {w for w in _words(b) if len(w) >= 3}
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / len(wa | wb) >= 0.5


def transport(mock: MockJev | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(mock or MockJev())


@dataclass
class Server:
    url: str
    mock: MockJev
    shutdown: Callable[[], None]


def serve(mock: MockJev | None = None, *, latency: float = 0.0) -> Server:
    """Run the mock as a real System One endpoint on localhost, for subprocess tests."""
    handler_mock = mock or MockJev()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if latency:
                threading.Event().wait(latency)
            try:
                body = json.loads(raw)
            except ValueError:
                status, payload = 400, {"error": {"message": "bad json"}}
            else:
                status, payload = handler_mock.handle(body, dict(self.headers.items()))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:  # silence
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def shutdown() -> None:
        server.shutdown()
        server.server_close()

    return Server(f"http://127.0.0.1:{port}/v1/systemone", handler_mock, shutdown)


def sample_lines(kind: str) -> Sequence[str]:
    """Small corpora shared by tests and docs."""
    if kind == "feedback":
        return (
            "Love the new dashboard, thanks team!",
            "This is the third time checkout has failed, I am done with this app!!",
            "How do I export my data to CSV?",
            "WHY does it log me out every five minutes??",
            "Pricing question: do you offer enterprise seats?",
        )
    if kind == "logs":
        return (
            "INFO  request served in 12ms",
            "ERROR payment service failed: connection refused",
            "INFO  cache warm",
            "WARN  disk temperature 71C on nvme0",
            "ERROR unauthorized access attempt from 10.0.0.9",
        )
    raise KeyError(kind)
