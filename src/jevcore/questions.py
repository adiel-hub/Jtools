"""The three Jev primitives and their typed answers.

Jev is not a text generator. It takes a *state* (text or JSON) plus typed questions and returns
typed answers with calibrated probabilities. Everything in j-tools is built from these three:

- :class:`Noul`   is this statement true?         -> a probability from 0 to 1
- :class:`Choice` which option fits best?         -> the option plus a distribution over options
- :class:`Score`  where on this ordered scale?    -> an interpolated position plus a distribution

The wire formats differ between TypeSafe's API and the Vercel AI Gateway; see :mod:`jevcore.wire`.
These dataclasses are the backend-neutral form the tools work with.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

State: TypeAlias = str | Mapping[str, Any] | Sequence[Any]
"""What Jev is shown. A string, a JSON object or a JSON array; Jev reads all three natively."""


@dataclass(frozen=True, slots=True)
class Noul:
    """A yes/no proposition about the state. Answer: the probability that it is true."""

    instructions: str
    criteria: Mapping[str, str] | None = None
    """Optional ``{"true": ..., "false": ...}`` descriptions that pin down the two cases."""

    def __post_init__(self) -> None:
        if not self.instructions.strip():
            raise ValueError("a noul question needs instructions")
        if self.criteria is not None and set(self.criteria) - {"true", "false"}:
            raise ValueError('noul criteria may only have the keys "true" and "false"')


@dataclass(frozen=True, slots=True)
class Choice:
    """Pick one option from a named set. Answer: the option and a probability for each option."""

    instructions: str
    options: Mapping[str, str]
    """Option name -> description. Names come back verbatim in the answer."""

    def __post_init__(self) -> None:
        if not self.instructions.strip():
            raise ValueError("a choice question needs instructions")
        if len(self.options) < 2:
            raise ValueError("a choice question needs at least two options")
        for name in self.options:
            if not name or not str(name).strip():
                raise ValueError("choice option names must not be blank")


@dataclass(frozen=True, slots=True)
class Score:
    """Rate the state on an ordered scale. Answer: an interpolated score plus a distribution."""

    instructions: str
    levels: Sequence[str]
    """Ordered from lowest to highest. At least two."""

    def __post_init__(self) -> None:
        if not self.instructions.strip():
            raise ValueError("a score question needs instructions")
        if len(self.levels) < 2:
            raise ValueError("a score question needs at least two levels")
        object.__setattr__(self, "levels", tuple(self.levels))


Question: TypeAlias = Noul | Choice | Score


@dataclass(frozen=True, slots=True)
class NoulAnswer:
    probability: float

    @property
    def value(self) -> float:
        return self.probability


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float | None = None

    @property
    def value(self) -> str:
        return self.choice

    @property
    def probability(self) -> float:
        """Probability of the chosen option."""
        return float(self.probabilities.get(self.choice, 0.0))

    def ranked(self) -> list[tuple[str, float]]:
        """Options from most to least probable; ties keep the option order of the request."""
        return sorted(self.probabilities.items(), key=lambda kv: -kv[1])


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    score: float
    """Probability-weighted position on the scale, from 0 to ``len(levels) - 1``."""
    probabilities: Mapping[int, float]
    levels: int
    confidence: float | None = None
    legend: Mapping[int, str] = field(default_factory=dict)

    @property
    def value(self) -> float:
        return self.score

    @property
    def normalized(self) -> float:
        """The score rescaled to 0..1 so that different rubrics compare."""
        if self.levels <= 1:
            return 0.0
        return min(1.0, max(0.0, self.score / (self.levels - 1)))

    @property
    def level(self) -> int:
        """The most probable rung."""
        if not self.probabilities:
            return round(self.score)
        return max(self.probabilities.items(), key=lambda kv: kv[1])[0]


Answer: TypeAlias = NoulAnswer | ChoiceAnswer | ScoreAnswer


def canonical(question: Question) -> str:
    """A stable text form of a question, used for cache keys and dry-run output."""
    if isinstance(question, Noul):
        payload: dict[str, Any] = {"type": "noul", "instructions": question.instructions}
        if question.criteria:
            payload["criteria"] = dict(question.criteria)
    elif isinstance(question, Choice):
        payload = {"type": "choice", "instructions": question.instructions, "options": dict(question.options)}
    else:
        payload = {"type": "score", "instructions": question.instructions, "levels": list(question.levels)}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def canonical_state(state: State) -> str:
    return state if isinstance(state, str) else json.dumps(state, sort_keys=True, ensure_ascii=False)
