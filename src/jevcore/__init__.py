"""jevcore: the shared client library behind j-tools.

from jevcore import Jev, Noul, Choice, Score, resolve

async with Jev(resolve()) as jev:
    answers = await jev.ask("checkout failed three times, I am done", {
        "angry": Noul("The writer is frustrated."),
        "route": Choice("Route this ticket.", {"billing": "payments", "bug": "defects"}),
        "urgency": Score("How urgent is this?", ["low", "medium", "high"]),
    })
    answers["angry"].probability      # 0.97
    answers["route"].choice           # "bug"
    answers["urgency"].normalized     # 0.93
"""

from __future__ import annotations

__version__ = "0.1.0"

from .auth import Credentials, available, resolve
from .backends import BACKENDS, Backend
from .client import Jev, Meter
from .errors import AuthError, BudgetExceeded, JevError, JevFatal, UsageError
from .questions import (
    Answer,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    State,
)

__all__ = [
    "BACKENDS",
    "Answer",
    "AuthError",
    "Backend",
    "BudgetExceeded",
    "Choice",
    "ChoiceAnswer",
    "Credentials",
    "Jev",
    "JevError",
    "JevFatal",
    "Meter",
    "Noul",
    "NoulAnswer",
    "Question",
    "Score",
    "ScoreAnswer",
    "State",
    "UsageError",
    "__version__",
    "available",
    "resolve",
]
