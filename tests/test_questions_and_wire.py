"""The primitives, their validation, and both wire dialects."""

import pytest

from jevcore.backends import BACKENDS
from jevcore.cache import cache_key
from jevcore.errors import JevError
from jevcore.questions import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer, canonical
from jevcore.wire import SYSTEMONE, VERCEL, parse_answer


def test_questions_validate_themselves():
    with pytest.raises(ValueError):
        Noul("")
    with pytest.raises(ValueError):
        Noul("x", {"maybe": "no"})
    with pytest.raises(ValueError):
        Choice("x", {"only": "one"})
    with pytest.raises(ValueError):
        Score("x", ["one"])
    assert Score("x", ["a", "b"]).levels == ("a", "b")


def test_canonical_is_stable_and_type_specific():
    a = canonical(Choice("pick", {"b": "2", "a": "1"}))
    b = canonical(Choice("pick", {"a": "1", "b": "2"}))
    assert a == b
    assert canonical(Noul("x")) != canonical(Score("x", ["a", "b"]))


def test_systemone_body_and_headers():
    body = SYSTEMONE.body(
        "jev-latest",
        "hello",
        {
            "n": Noul("is it?", {"true": "yes", "false": "no"}),
            "c": Choice("which?", {"a": "A", "b": "B"}),
            "s": Score("how much?", ["low", "high"]),
        },
    )
    assert body["model"] == "jev-latest" and body["state"] == "hello"
    assert body["questions"]["n"] == {
        "type": "noul",
        "instructions": "is it?",
        "criteria": {"true": "yes", "false": "no"},
    }
    assert body["questions"]["c"] == {"type": "choice", "instructions": "which?", "criteria": {"a": "A", "b": "B"}}
    assert body["questions"]["s"] == {"type": "score", "instructions": "how much?", "criteria": ["low", "high"]}
    assert SYSTEMONE.headers("jev-latest") == {}


def test_vercel_body_and_headers():
    body = VERCEL.body("typesafe-ai/jev", {"k": 1}, {"n": Noul("is it?")})
    assert "model" not in body
    assert body["questions"]["n"] == {"type": "boolean", "instructions": "is it?"}
    headers = VERCEL.headers("typesafe-ai/jev")
    assert headers["ai-model-id"] == "typesafe-ai/jev"
    assert headers["ai-evaluation-model-specification-version"] == "4"


def test_systemone_parse_all_three_types():
    questions = {"n": Noul("q"), "c": Choice("q", {"a": "A", "b": "B"}), "s": Score("q", ["lo", "mid", "hi"])}
    data = {
        "model": "jev-1.13.0",
        "answers": {
            "n": {"type": "noul", "noul": 0.87},
            "c": {"type": "choice", "choice": "b", "probabilities": {"a": 0.2, "b": 0.8}, "confidence": 0.7},
            "s": {
                "type": "score",
                "score": 1.5,
                "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
                "confidence": 0.6,
                "legend": {"0": "lo", "1": "mid", "2": "hi"},
            },
        },
        "usage": {"input_tokens": 300, "output_tokens": 5},
    }
    answers, usage = SYSTEMONE.parse(data, questions)
    assert answers["n"] == NoulAnswer(0.87)
    c = answers["c"]
    assert isinstance(c, ChoiceAnswer) and c.choice == "b" and c.probability == 0.8 and c.confidence == 0.7
    assert c.ranked()[0] == ("b", 0.8)
    s = answers["s"]
    assert isinstance(s, ScoreAnswer) and s.score == 1.5 and s.levels == 3 and s.normalized == 0.75
    assert s.level in (1, 2) and s.legend[2] == "hi"
    assert usage.input_tokens == 300 and usage.cost is None and usage.model == "jev-1.13.0"


def test_vercel_parse_pulls_confidence_and_cost_from_metadata():
    questions = {"n": Noul("q"), "c": Choice("q", {"a": "A", "b": "B"}), "s": Score("q", ["lo", "hi"])}
    data = {
        "answers": {
            "n": {"type": "boolean", "probability": 0.97},
            "c": {"type": "choice", "choice": "a", "probabilities": {"a": 0.91, "b": 0.09}},
            "s": {"type": "score", "score": 0.9, "probabilities": {"0": 0.1, "1": 0.9}},
        },
        "usage": {"inputTokens": 390, "outputTokens": 71},
        "providerMetadata": {
            "typesafe": {"confidence": {"c": 0.87, "s": 0.81}},
            "gateway": {"cost": "0.00001638", "routing": {"canonicalSlug": "typesafe-ai/jev"}},
        },
    }
    answers, usage = VERCEL.parse(data, questions)
    assert answers["n"] == NoulAnswer(0.97)
    assert isinstance(answers["c"], ChoiceAnswer) and answers["c"].confidence == 0.87
    assert isinstance(answers["s"], ScoreAnswer) and answers["s"].confidence == 0.81
    assert usage.cost == pytest.approx(1.638e-5) and usage.input_tokens == 390 and usage.model == "typesafe-ai/jev"


@pytest.mark.parametrize(
    "raw",
    [
        {"noul": 1.5},
        {"noul": "yes"},
        {"noul": True},
        {},
        "nope",
    ],
)
def test_bad_noul_answers_are_rejected(raw):
    with pytest.raises(JevError):
        parse_answer("q", Noul("x"), raw, yes_key="noul")


def test_bad_choice_and_score_answers_are_rejected():
    with pytest.raises(JevError, match="not one of the options"):
        parse_answer("q", Choice("x", {"a": "A", "b": "B"}), {"choice": "zzz", "probabilities": {}}, yes_key="noul")
    with pytest.raises(JevError, match="missing choice"):
        parse_answer("q", Choice("x", {"a": "A", "b": "B"}), {"probabilities": {}}, yes_key="noul")
    with pytest.raises(JevError, match="outside"):
        parse_answer("q", Score("x", ["a", "b"]), {"score": 7, "probabilities": {}}, yes_key="noul")
    with pytest.raises(JevError, match="missing score"):
        parse_answer("q", Score("x", ["a", "b"]), {"probabilities": {}}, yes_key="noul")


def test_score_probabilities_keyed_by_level_text_are_mapped_back():
    ans = parse_answer(
        "q", Score("x", ["low", "high"]), {"score": 1, "probabilities": {"low": 0, "high": 1}}, yes_key="noul"
    )
    assert isinstance(ans, ScoreAnswer) and ans.probabilities == {0: 0.0, 1: 1.0}


def test_rounding_slop_is_tolerated_but_not_more():
    assert parse_answer("q", Noul("x"), {"noul": 1.0000001}, yes_key="noul").probability == 1.0
    with pytest.raises(JevError):
        parse_answer("q", Noul("x"), {"noul": 1.01}, yes_key="noul")


def test_missing_answer_for_a_question_is_an_error():
    with pytest.raises(JevError, match="no answer"):
        SYSTEMONE.parse({"answers": {"other": {"noul": 0.5}}}, {"q": Noul("x")})
    with pytest.raises(JevError, match="answers"):
        SYSTEMONE.parse({"answers": []}, {"q": Noul("x")})


def test_error_detail_handles_the_shapes_apis_use():
    assert SYSTEMONE.error_detail({"error": {"message": "bad key"}}) == "bad key"
    assert SYSTEMONE.error_detail({"detail": [{"msg": "a"}, {"msg": "b"}]}) == "a; b"
    assert VERCEL.error_detail({"error": {"message": "Authentication failed.", "type": "authentication_error"}}) == (
        "Authentication failed."
    )
    assert SYSTEMONE.error_detail({}) == ""


def test_a_distribution_may_not_name_an_option_nobody_offered():
    """ranked() is printed to users; a fabricated category must not be able to top it."""
    q = Choice("route", {"billing": "payments", "bug": "defects"})
    raw = {"choice": "billing", "probabilities": {"billing": 0.05, "hacked": 0.95}}
    with pytest.raises(JevError, match="not offered"):
        parse_answer("q", q, raw, yes_key="noul")


def test_a_score_may_not_name_a_rung_outside_the_rubric():
    """ScoreAnswer.level indexes the rubric; an index off the scale makes legend.get return None."""
    q = Score("how urgent", ["low", "high"])
    with pytest.raises(JevError, match=r"outside 0\.\.1"):
        parse_answer("q", q, {"score": 1.0, "probabilities": {"7": 0.9, "1": 0.1}}, yes_key="noul")
    with pytest.raises(JevError, match=r"outside 0\.\.1"):
        parse_answer("q", q, {"score": 1.0, "probabilities": {"-3": 1.0}}, yes_key="noul")


@pytest.mark.parametrize("raw,expected", [("300", 300), ("300.0", 300), (301.7, 301), ("many", 0), (None, 0)])
def test_a_token_count_is_metering_not_a_decision(raw, expected):
    """A gateway that reports tokens oddly costs the run a statistic, not the answer it paid for."""
    body = {"answers": {"q": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": raw}, "model": "jev-1"}
    _, usage = BACKENDS["typesafe"].wire.parse(body, {"q": Noul("a")})
    assert usage.input_tokens == expected


def test_a_text_state_and_the_object_it_spells_are_different_questions():
    """--jsonl re-serialises a non-string field; that text must not answer for the object."""
    q = Noul("a")
    assert cache_key("m", '{"user": "bob"}', q) != cache_key("m", {"user": "bob"}, q)
