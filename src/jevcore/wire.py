"""Wire formats: how questions and answers look on each API.

Two dialects exist today:

- **System One** (``POST /v1/systemone``): TypeSafe's own API, OpenRouter's decisions endpoint and
  any gateway that proxies it. Question types are ``noul``/``choice``/``score``; the body carries
  ``model``; answers come back as ``{"noul": p}``, ``{"choice", "probabilities", "confidence"}``
  and ``{"score", "probabilities", "confidence", "legend"}``; usage is ``input_tokens``.
- **Vercel AI Gateway** (``POST /v4/ai/evaluation-model``): the AI SDK's evaluation modality. The
  yes/no type is called ``boolean`` and answers ``{"probability": p}``; the model travels in an
  ``ai-model-id`` header; confidence lives under ``providerMetadata.typesafe.confidence`` and the
  billed cost under ``providerMetadata.gateway.cost``.

Everything else in j-tools speaks :mod:`jevcore.questions`; only this module knows about JSON shapes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import JevError
from .questions import (
    Answer,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    ScoreAnswer,
    State,
)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    """Dollars, when the API reports them (OpenRouter, Vercel). TypeSafe reports tokens only."""
    model: str | None = None
    """The versioned model that answered, when reported. Resolves aliases like ``jev-latest``."""


class Wire(Protocol):
    name: str

    def headers(self, model: str) -> dict[str, str]: ...

    def body(self, model: str, state: State, questions: Mapping[str, Question]) -> dict[str, Any]: ...

    def parse(self, data: dict[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], Usage]: ...

    def error_detail(self, data: dict[str, Any]) -> str: ...


def _probability(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise JevError(f"invalid answer: {what} must be a number, got {value!r}")
    if not 0.0 <= value <= 1.0:
        # Rounding on the wire can leave 1.0000001; anything further off is a real problem.
        if -1e-6 <= value <= 1 + 1e-6:
            return min(1.0, max(0.0, float(value)))
        raise JevError(f"invalid answer: {what} must be a probability from 0 to 1, got {value!r}")
    return float(value)


def _distribution(raw: Any, what: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise JevError(f"invalid answer: {what} probabilities must be an object")
    return {str(k): _probability(v, f"{what} probability for {k!r}") for k, v in raw.items()}


def _optional_confidence(value: Any) -> float | None:
    if value is None:
        return None
    return _probability(value, "confidence")


def _count(raw: Any) -> int:
    """A token count from a gateway that may send ``300``, ``"300"``, ``"300.0"`` or nonsense.

    A count is metering, never a decision, so a value that makes no sense costs the run a
    statistic, not the answer it just paid for.
    """
    if isinstance(raw, bool) or raw is None:
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def parse_answer(qid: str, question: Question, raw: Any, *, yes_key: str, confidence: float | None = None) -> Answer:
    """Turn one raw answer object into a typed answer, validating it against the question."""
    if not isinstance(raw, dict):
        raise JevError(f"invalid answer for {qid!r}: expected an object")
    if isinstance(question, Noul):
        if yes_key not in raw:
            raise JevError(f"invalid answer for {qid!r}: missing {yes_key!r}")
        return NoulAnswer(_probability(raw[yes_key], f"{qid} probability"))
    if isinstance(question, Choice):
        choice = raw.get("choice")
        if not isinstance(choice, str):
            raise JevError(f"invalid answer for {qid!r}: missing choice")
        probabilities = _distribution(raw.get("probabilities") or {}, qid)
        if choice not in question.options:
            raise JevError(f"invalid answer for {qid!r}: {choice!r} is not one of the options")
        # The distribution is checked as strictly as the choice: ChoiceAnswer.ranked() is what
        # tools print, and an option nobody offered must not be able to appear at the top of it.
        offered = {str(name) for name in question.options}
        if unknown := sorted(set(probabilities) - offered):
            raise JevError(f"invalid answer for {qid!r}: probabilities for options not offered: {', '.join(unknown)}")
        for name in offered:
            probabilities.setdefault(name, 0.0)
        return ChoiceAnswer(choice, probabilities, _optional_confidence(raw.get("confidence", confidence)))
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
        raise JevError(f"invalid answer for {qid!r}: missing score")
    n = len(question.levels)
    if not -1e-6 <= score <= n - 1 + 1e-6:
        raise JevError(f"invalid answer for {qid!r}: score {score!r} is outside 0..{n - 1}")
    dist = _distribution(raw.get("probabilities") or {}, qid)
    by_index: dict[int, float] = {}
    for key, p in dist.items():
        try:
            idx = int(key)
        except ValueError:
            # TypeSafe may key rungs by their text; map it back through the levels.
            if key in question.levels:
                idx = list(question.levels).index(key)
            else:
                raise JevError(f"invalid answer for {qid!r}: unknown level {key!r}") from None
        if not 0 <= idx < n:
            # ScoreAnswer.level picks the most probable rung, so an index off the scale used to
            # make it name a rung the rubric does not have and legend.get return None. The score
            # itself is range-checked above and still usable, so an unusable rung is dropped
            # rather than thrown away with the answer: a backend that numbered its rungs from one
            # would otherwise turn every score into an unjudged line.
            continue
        by_index[idx] = p
    for i in range(n):
        by_index.setdefault(i, 0.0)
    legend_raw = raw.get("legend")
    legend: dict[int, str] = {}
    if isinstance(legend_raw, dict):
        for k, v in legend_raw.items():
            try:
                legend[int(k)] = str(v)
            except ValueError:
                continue
    if not legend:
        legend = dict(enumerate(question.levels))
    return ScoreAnswer(
        score=min(float(n - 1), max(0.0, float(score))),
        probabilities=dict(sorted(by_index.items())),
        levels=n,
        confidence=_optional_confidence(raw.get("confidence", confidence)),
        legend=legend,
    )


def _obj(raw: Any) -> dict[str, Any]:
    """A response field that should be an object, or an empty one.

    Usage and provider metadata are read for their keys. `or {}` covers a field that is missing,
    null or empty -- including `[]` -- but not one that holds something of the wrong type: a
    gateway or proxy that reports usage as `"none"`, or confidence as a string, used to turn
    metering into an AttributeError over a response whose answers were perfectly good.
    """
    return raw if isinstance(raw, dict) else {}


def _detail(found: Any) -> str:
    if isinstance(found, list):
        return "; ".join(_detail(item) for item in found)
    if isinstance(found, dict):
        return str(found.get("message") or found.get("msg") or found.get("detail") or found)
    return str(found or "")


class SystemOneWire:
    """TypeSafe's native format, also spoken by OpenRouter and by System One gateways."""

    name = "systemone"

    def headers(self, model: str) -> dict[str, str]:
        return {}

    def body(self, model: str, state: State, questions: Mapping[str, Question]) -> dict[str, Any]:
        return {"model": model, "state": state, "questions": {qid: self.question(q) for qid, q in questions.items()}}

    @staticmethod
    def question(q: Question) -> dict[str, Any]:
        if isinstance(q, Noul):
            out: dict[str, Any] = {"type": "noul", "instructions": q.instructions}
            if q.criteria:
                out["criteria"] = dict(q.criteria)
            return out
        if isinstance(q, Choice):
            return {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
        return {"type": "score", "instructions": q.instructions, "criteria": list(q.levels)}

    def parse(self, data: dict[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], Usage]:
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise JevError("invalid response: expected an 'answers' object")
        out: dict[str, Answer] = {}
        for qid, q in questions.items():
            if qid not in answers:
                raise JevError(f"invalid response: no answer for question {qid!r}")
            out[qid] = parse_answer(qid, q, answers[qid], yes_key="noul")
        usage_raw = _obj(data.get("usage"))
        cost = usage_raw.get("cost")
        usage = Usage(
            input_tokens=_count(usage_raw.get("input_tokens")),
            output_tokens=_count(usage_raw.get("output_tokens")),
            cost=float(cost) if isinstance(cost, int | float) else None,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
        )
        return out, usage

    def error_detail(self, data: dict[str, Any]) -> str:
        return " ".join(_detail(data.get("error", data.get("detail"))).split())[:300]


class VercelWire:
    """The Vercel AI Gateway evaluation modality (AI SDK ``experimental_evaluate``)."""

    name = "vercel"
    SPEC_VERSION = "4"
    PROTOCOL_VERSION = "0.0.1"

    def headers(self, model: str) -> dict[str, str]:
        return {
            "ai-gateway-protocol-version": self.PROTOCOL_VERSION,
            "ai-evaluation-model-specification-version": self.SPEC_VERSION,
            "ai-model-id": model,
        }

    def body(self, model: str, state: State, questions: Mapping[str, Question]) -> dict[str, Any]:
        return {"state": state, "questions": {qid: self.question(q) for qid, q in questions.items()}}

    @staticmethod
    def question(q: Question) -> dict[str, Any]:
        if isinstance(q, Noul):
            out: dict[str, Any] = {"type": "boolean", "instructions": q.instructions}
            if q.criteria:
                out["criteria"] = dict(q.criteria)
            return out
        if isinstance(q, Choice):
            return {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
        return {"type": "score", "instructions": q.instructions, "criteria": list(q.levels)}

    def parse(self, data: dict[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], Usage]:
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise JevError("invalid response: expected an 'answers' object")
        meta = _obj(data.get("providerMetadata"))
        confidences = _obj(_obj(meta.get("typesafe")).get("confidence"))
        out: dict[str, Answer] = {}
        for qid, q in questions.items():
            if qid not in answers:
                raise JevError(f"invalid response: no answer for question {qid!r}")
            conf = confidences.get(qid)
            out[qid] = parse_answer(qid, q, answers[qid], yes_key="probability", confidence=conf)
        usage_raw = _obj(data.get("usage"))
        gateway = _obj(meta.get("gateway"))
        cost_raw = gateway.get("cost")
        cost: float | None
        try:
            cost = float(cost_raw) if cost_raw is not None else None
        except (TypeError, ValueError):
            cost = None
        routing = _obj(gateway.get("routing"))
        model = routing.get("canonicalSlug") if isinstance(routing.get("canonicalSlug"), str) else None
        usage = Usage(
            input_tokens=_count(usage_raw.get("inputTokens")),
            output_tokens=_count(usage_raw.get("outputTokens")),
            cost=cost,
            model=model,
        )
        return out, usage

    def error_detail(self, data: dict[str, Any]) -> str:
        return " ".join(_detail(data.get("error", data.get("detail"))).split())[:300]


SYSTEMONE = SystemOneWire()
VERCEL = VercelWire()
