"""Ordered concurrent judging, halting, blank lines, input errors, fatal errors."""

import asyncio
import random

import pytest

from jevcore.errors import JevFatal
from jevcore.inputs import InputError, Record
from jevcore.pipeline import Pipeline, collect


def make_records(n: int):
    return [Record(i, f"line {i}", "x", i + 1) for i in range(n)]


async def test_delivery_is_in_input_order_despite_random_latency():
    seen = []

    async def judge(rec):
        await asyncio.sleep(random.random() * 0.02)
        return rec.seq * 10

    def deliver(rec, value):
        seen.append((rec.seq, value))

    result = await Pipeline(concurrency=8).run(make_records(50), judge, deliver)
    assert seen == [(i, i * 10) for i in range(50)]
    assert result.seen == 50 and result.judged == 50 and result.tasks_peak <= 8 and not result.halted


async def test_unordered_delivers_as_results_arrive():
    seen = []

    async def judge(rec):
        await asyncio.sleep(0.03 if rec.seq == 0 else 0)
        return rec.seq

    result = await Pipeline(concurrency=8, ordered=False).run(make_records(5), judge, lambda r, v: seen.append(v))
    assert sorted(seen) == [0, 1, 2, 3, 4] and seen[-1] == 0 and result.judged == 5


async def test_blank_lines_are_not_judged():
    calls = []
    records = [Record(0, "a", "x", 1), Record(1, "   ", "x", 2), Record(2, "b", "x", 3)]

    async def judge(rec):
        calls.append(rec.text)
        return True

    seen = []
    await Pipeline().run(records, judge, lambda r, v: seen.append((r.text, v)))
    assert calls == ["a", "b"] and seen == [("a", True), ("   ", None), ("b", True)]


async def test_halt_stops_reading_and_cancels_the_rest():
    produced = []

    def source():
        for i in range(10_000):
            produced.append(i)
            yield Record(i, f"line {i}", "x", i + 1)

    pipe: Pipeline[int] = Pipeline(concurrency=4)
    seen = []

    async def judge(rec):
        await asyncio.sleep(0.005)
        return rec.seq

    def deliver(rec, value):
        seen.append(value)
        if len(seen) == 5:
            pipe.halt()

    result = await pipe.run(source(), judge, deliver)
    assert seen == [0, 1, 2, 3, 4] and result.halted and len(produced) < 200


async def test_input_errors_are_reported_and_skipped():
    errors = []
    items = [Record(0, "a", "x", 1), InputError("bad file"), Record(1, "b", "x", 2)]
    seen = []
    result = await Pipeline().run(
        items, lambda r: asyncio.sleep(0, result=r.text), lambda r, v: seen.append(v), errors.append
    )
    assert seen == ["a", "b"] and [e.message for e in errors] == ["bad file"] and result.input_errors == 1


async def test_fatal_from_judge_halts_and_is_returned():
    async def judge(rec):
        if rec.seq == 3:
            raise JevFatal("no credits")
        await asyncio.sleep(0.01)
        return rec.seq

    result = await Pipeline(concurrency=2).run(make_records(20), judge, lambda r, v: None)
    assert isinstance(result.fatal, JevFatal) and result.halted and result.judged < 20


async def test_unexpected_exception_in_judge_yields_none_for_that_record():
    async def judge(rec):
        if rec.seq == 1:
            raise RuntimeError("boom")
        return rec.seq

    seen = []
    result = await Pipeline().run(make_records(3), judge, lambda r, v: seen.append(v))
    assert seen == [0, None, 2] and result.errors and "boom" in result.errors[0]


async def test_exception_in_deliver_ends_the_run():
    def deliver(rec, value):
        raise ValueError("cannot write")

    result = await Pipeline().run(make_records(3), lambda r: asyncio.sleep(0, result=1), deliver)
    assert isinstance(result.fatal, ValueError)


async def test_collect_reads_everything_and_reports_errors():
    errors = []
    items = [Record(0, "a", "x", 1), InputError("oops"), Record(1, "b", "x", 2)]
    recs = await collect(items, errors.append)
    assert [r.text for r in recs] == ["a", "b"] and len(errors) == 1


@pytest.mark.timeout(10)
async def test_backpressure_does_not_read_far_ahead_of_judging():
    """The reader must not slurp a live pipe: at most ~2x concurrency records ahead of delivery."""
    produced = 0
    delivered = 0
    gap_max = 0

    def source():
        nonlocal produced
        for i in range(300):
            produced += 1
            yield Record(i, f"l{i}", "x", i + 1)

    async def judge(rec):
        nonlocal gap_max
        gap_max = max(gap_max, produced - delivered)
        await asyncio.sleep(0.001)
        return 1

    def deliver(rec, value):
        nonlocal delivered
        delivered += 1

    await Pipeline(concurrency=5).run(source(), judge, deliver)
    assert gap_max <= 5 * 3


async def test_a_gap_in_the_record_numbering_still_delivers_everything():
    """Ordering counts arrivals, not Record.seq.

    ``iter_records`` skips blank lines but still spends their sequence number, and a tool may
    filter before the pipeline. A hole in seq used to stall ordered delivery for good: everything
    after it was judged, paid for, and thrown away, with judged counted and no error reported.
    """
    records = [Record(0, "one"), Record(1, "two"), Record(3, "four"), Record(9, "nine")]
    seen: list[str] = []

    async def judge(rec):
        await asyncio.sleep(random.random() * 0.01)
        return rec.text.upper()

    result = await Pipeline(concurrency=4).run(records, judge, lambda rec, v: seen.append(v))
    assert seen == ["ONE", "TWO", "FOUR", "NINE"]
    assert result.judged == 4 and not result.halted and result.fatal is None


async def test_delivery_starts_at_a_nonzero_first_record():
    """A tool that skips a header still gets its first record delivered, not held for seq 0."""
    records = [Record(5, "five"), Record(6, "six")]
    seen: list[str] = []
    await Pipeline(concurrency=2).run(records, _echo, lambda rec, v: seen.append(v))
    assert seen == ["five", "six"]


async def _echo(rec):
    return rec.text
