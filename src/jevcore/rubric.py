"""How the tools phrase their questions. One place, so every tool asks Jev the same way.

Jev answers the description you wrote, not the one you meant; these builders keep the wrapping
around the user's words minimal and consistent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .errors import UsageError
from .questions import Choice, Noul, Score

FIT_LEVELS: tuple[str, ...] = (
    "does not fit the description at all",
    "fits the description slightly",
    "fits the description moderately",
    "fits the description strongly",
    "fits the description perfectly",
)

NONE_OPTION = "none"


def fits(description: str) -> Noul:
    """jgrep / jgate / jwatch: is this text an instance of the description?"""
    return Noul(f'The text fits this description: "{description}"')


def fits_in_context(description: str) -> Noul:
    """jgrep -C: the marked lines are judged; the rest is context."""
    return Noul(
        f'The lines marked ">" fit this description: "{description}". The other lines are the surrounding '
        "text, shown only so the marked lines can be read in context; they are not themselves being judged."
    )


def fit_score(description: str, levels: Sequence[str] | None = None) -> Score:
    """jsort / jhead / jtag --score: how well does the text fit, on an ordered scale?"""
    return Score(f'Rate how well the text fits this description: "{description}".', tuple(levels or FIT_LEVELS))


def same_meaning(description: str, index: int) -> Noul:
    """juniq: does the candidate mean the same as kept line *index*?"""
    return Noul(
        f'The "candidate" text and "kept"[{index}] are duplicates in this sense: "{description}". '
        "Different wording of the same thing counts as a duplicate; a different thing does not."
    )


def candidates_state(texts: Iterable[str], ids: Iterable[str]) -> list[dict[str, str]]:
    return [{"id": cid, "text": text} for cid, text in zip(ids, texts, strict=True)]


def pick(description: str, ids: Sequence[str], *, allow_none: bool = False) -> Choice:
    """jpick / jmatch: which candidate best fits the description?"""
    options = {cid: f"the candidate whose id is {cid}" for cid in ids}
    if allow_none:
        options[NONE_OPTION] = "no candidate fits the description"
    return Choice(f'Choose the candidate that best fits this description: "{description}".', options)


def match(description: str, ids: Sequence[str]) -> Choice:
    """jmatch: which record in "candidates" matches "target" (or none)?"""
    options = {cid: f'the candidate whose id is {cid} matches "target"' for cid in ids}
    options[NONE_OPTION] = 'no candidate matches "target"'
    return Choice(
        f'Which of the "candidates" goes with the "target" in this sense: "{description}"? '
        f'Pick "{NONE_OPTION}" if none of them does.',
        options,
    )


def classify(labels: dict[str, str], instructions: str | None = None) -> Choice:
    """jtag --labels / jroute: which label applies?"""
    return Choice(instructions or "Which label describes the text best?", labels)


_NUM = r"(-?\d+(?:\.\d+)?)"
_BARE_RANGE = re.compile(rf"^\s*{_NUM}\s*(?:-|\u2013|to)\s*{_NUM}\s*$")
# In a description, only a parenthesised range or an explicit "from X to Y" counts, so that
# "how relevant to the 2024-2025 roadmap" is not read as a scale.
_DESC_RANGE = re.compile(rf"\(\s*{_NUM}\s*(?:-|\u2013|to)\s*{_NUM}\s*\)|\bfrom\s+{_NUM}\s+to\s+{_NUM}\b", re.I)


def parse_scale(text: str, *, bare: bool = False) -> tuple[float, float] | None:
    """``"how positive (0-100)"`` -> ``(0.0, 100.0)``; ``None`` when no range is written.

    With ``bare=True`` (the ``--scale`` flag) the whole text must be the range, e.g. ``1-5``.
    """
    m = _BARE_RANGE.match(text) if bare else _DESC_RANGE.search(text)
    if not m:
        return None
    numbers = [g for g in m.groups() if g is not None]
    lo, hi = float(numbers[0]), float(numbers[1])
    return (lo, hi) if lo != hi else None


NAME = r"[A-Za-z0-9._-]+"
_LABEL_START = re.compile(rf"(?:^|,)\s*({NAME})\s*:")
_PLAIN_ITEM = re.compile(rf"^\s*{NAME}\s*(?::|$)")
_ESCAPED_COMMA = re.compile(r"\\,")


def _split_commas(text: str) -> list[str]:
    """Split on commas, except ``\\,`` which means a comma inside a description."""
    return [part.replace("\0", ",") for part in _ESCAPED_COMMA.sub("\0", text).split(",")]


def parse_levels(text: str) -> tuple[str, ...]:
    """``"low,medium,high"`` -> the rungs, lowest first. ``\\,`` is a comma inside one rung."""
    levels = tuple(part.strip() for part in _split_commas(text) if part.strip())
    if len(levels) < 2:
        raise UsageError("--levels needs at least two comma-separated levels, lowest first")
    return levels


def parse_labels(text: str) -> dict[str, str]:
    """``"bug,feature:new capability,question"`` -> ``{"bug": "bug", "feature": "new capability", …}``.

    A description may itself contain commas (``"world:politics, war and diplomacy,sports:games"``):
    when a comma-separated item does not start a new ``name:``, it belongs to the description
    before it. ``\\,`` is always a literal comma. Names may use letters, digits, dot, dash and
    underscore, which is what keeps the two readings apart.
    """
    labels: dict[str, str] = {}

    def add(name: str, description: str) -> None:
        name = name.strip()
        if not name:
            raise UsageError(f"label {description.strip()!r} has no name")
        if not re.fullmatch(NAME, name):
            raise UsageError(f"label name {name!r} may only use letters, digits, dot, dash and underscore")
        if name in labels:
            raise UsageError(f"duplicate label {name!r}")
        labels[name] = _ESCAPED_COMMA.sub(",", description).strip() or name

    items = _split_commas(text)
    if all(_PLAIN_ITEM.match(item) for item in items if item.strip()):
        # Every item names a label, so each item is one label: "bug,feature:new capability".
        for item in items:
            if item.strip():
                name, _, description = item.partition(":")
                add(name, description)
    else:
        # Some item is not a label of its own, so it continues the description before it.
        starts = list(_LABEL_START.finditer(text))
        if not starts:
            raise UsageError(f"cannot read labels from {text!r}; use name:description separated by commas")
        for item in _split_commas(text[: starts[0].start()]):
            if item.strip():
                add(item, "")
        for i, start in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
            add(start.group(1), text[start.end() : end])
    if len(labels) < 2:
        raise UsageError("need at least two labels")
    return labels


def parse_buckets(specs: Sequence[str], *, what: str = "bucket") -> dict[str, str]:
    """One ``name:description`` per argument, so a description may contain anything."""
    buckets: dict[str, str] = {}
    for spec in specs:
        name, sep, desc = spec.partition(":")
        name = name.strip()
        if not sep or not name or not desc.strip():
            raise UsageError(f'{what} {spec!r} must look like "name:description"')
        if not re.fullmatch(NAME, name):
            raise UsageError(f"{what} name {name!r} may only use letters, digits, dot, dash and underscore")
        if name in buckets:
            raise UsageError(f"duplicate {what} {name!r}")
        buckets[name] = desc.strip()
    if len(buckets) < 2:
        raise UsageError(f"need at least two {what}s")
    return buckets


def ids_for(n: int, prefix: str = "c") -> list[str]:
    return [f"{prefix}{i + 1}" for i in range(n)]
