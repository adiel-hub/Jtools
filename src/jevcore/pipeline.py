"""Judge records concurrently, deliver results in input order, never buffer a live pipe.

The reader runs in a thread (it may block on ``tail -f``); records flow through a bounded queue
into judge tasks gated by a semaphore. Delivery restores input order unless the tool asks for
results as they arrive. Setting :meth:`Pipeline.halt` (``-m`` reached, ``-q`` matched, budget
spent) stops the reader, cancels outstanding requests and returns.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .errors import JevError
from .inputs import InputError, Item, Record

R = TypeVar("R")


@dataclass
class PipelineResult:
    seen: int = 0
    judged: int = 0
    input_errors: int = 0
    fatal: BaseException | None = None
    halted: bool = False
    truncated: int = 0
    tasks_peak: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline(Generic[R]):
    """``await Pipeline(concurrency=20).run(records, judge, deliver)``.

    ``judge(record)`` is awaited for every non-blank record (blank ones get ``None`` without a
    call). ``deliver(record, result)`` is called in input order (or arrival order with
    ``ordered=False``). Raise :class:`~jevcore.errors.JevFatal` from ``judge`` to stop the run.
    """

    def __init__(self, *, concurrency: int = 20, ordered: bool = True, judge_blank: bool = False) -> None:
        self.concurrency = max(1, concurrency)
        self.ordered = ordered
        self.judge_blank = judge_blank
        self._halt = asyncio.Event()
        self._stop = threading.Event()

    def halt(self) -> None:
        self._halt.set()
        self._stop.set()

    @property
    def stop_event(self) -> threading.Event:
        """Hand this to :func:`jevcore.inputs.iter_records` so a halted run releases the reader."""
        return self._stop

    async def run(
        self,
        source: Iterable[Item],
        judge: Callable[[Record], Awaitable[R]],
        deliver: Callable[[Record, R | None], None],
        on_input_error: Callable[[InputError], None] | None = None,
    ) -> PipelineResult:
        loop = asyncio.get_running_loop()
        result = PipelineResult()
        queue: asyncio.Queue[Item | None] = asyncio.Queue(maxsize=self.concurrency)
        sem = asyncio.Semaphore(self.concurrency)
        tasks: set[asyncio.Task[None]] = set()
        finished: dict[int, tuple[Record, R | None]] = {}
        # Ordering counts arrivals rather than trusting Record.seq to be contiguous: a source
        # that skips a record (a blank line, a filter upstream) leaves a hole in seq, and one
        # hole used to stall delivery for good, silently dropping everything judged after it.
        state = {"next": 0, "arrived": 0}
        inflight = {"n": 0}
        halted = asyncio.ensure_future(self._halt.wait())

        def feed() -> None:
            try:
                for item in source:
                    if self._stop.is_set():
                        return
                    fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
                    while True:
                        try:
                            fut.result(timeout=0.2)
                            break
                        except TimeoutError:
                            if self._stop.is_set():
                                fut.cancel()
                                return
            except Exception as e:  # reader failures are reported, not raised
                if not self._stop.is_set():
                    asyncio.run_coroutine_threadsafe(queue.put(InputError(f"input reader: {e}")), loop).result()
            finally:
                if not self._stop.is_set():
                    # The loop may already be gone; then there is nothing left to wake.
                    with contextlib.suppress(Exception):
                        asyncio.run_coroutine_threadsafe(queue.put(None), loop).result(timeout=5)

        def emit(record: Record, value: R | None) -> None:
            if self._halt.is_set():
                return
            try:
                deliver(record, value)
            except Exception as e:  # a failing deliver ends the run; nothing sensible remains
                result.fatal = result.fatal or e
                self.halt()

        def dispatch(record: Record, value: R | None, rank: int) -> None:
            result.judged += 1
            if not self.ordered:
                emit(record, value)
                return
            finished[rank] = (record, value)
            while state["next"] in finished and not self._halt.is_set():
                emit(*finished.pop(state["next"]))
                state["next"] += 1

        async def worker(record: Record, rank: int) -> None:
            try:
                value: R | None
                if record.is_blank() and not self.judge_blank:
                    value = None
                else:
                    inflight["n"] += 1
                    result.tasks_peak = max(result.tasks_peak, inflight["n"])
                    try:
                        value = await judge(record)
                    finally:
                        inflight["n"] -= 1
            except JevError as e:  # fatal, or a per-request error under --strict
                result.fatal = result.fatal or e
                self.halt()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # one bad record must not leave a gap in the order
                result.errors.append(f"{record.source}:{record.lineno}: {type(e).__name__}: {e}")
                value = None
            finally:
                sem.release()
            dispatch(record, value, rank)

        def done(task: asyncio.Task[None]) -> None:
            tasks.discard(task)
            if not task.cancelled() and (exc := task.exception()) is not None:
                result.fatal = result.fatal or exc
                self.halt()

        threading.Thread(target=feed, daemon=True, name="jev-reader").start()
        try:
            while not self._halt.is_set():
                getter = asyncio.ensure_future(queue.get())
                await asyncio.wait({getter, halted}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    break
                item = getter.result()
                if item is None:
                    break
                if isinstance(item, InputError):
                    result.input_errors += 1
                    if on_input_error is not None:
                        on_input_error(item)
                    continue
                result.seen += 1
                if item.truncated:
                    result.truncated += 1
                await sem.acquire()
                if self._halt.is_set():
                    sem.release()
                    break
                task = asyncio.create_task(worker(item, state["arrived"]))
                state["arrived"] += 1
                tasks.add(task)
                task.add_done_callback(done)
        finally:
            # Whatever ends the loop (EOF, halt, cancellation), the reader thread must be released.
            self._stop.set()
        pending = list(tasks)
        if pending:
            drained: asyncio.Future[Any] = asyncio.gather(*pending, return_exceptions=True)
            waiters: set[asyncio.Future[Any]] = {drained, halted}
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if self._halt.is_set():
                for t in pending:
                    t.cancel()
            await drained
        halted.cancel()
        await asyncio.gather(halted, return_exceptions=True)
        result.halted = self._halt.is_set()
        return result


async def collect(source: Iterable[Item], on_input_error: Callable[[InputError], None] | None = None) -> list[Record]:
    """Read everything (for tools whose nature needs all the input: sort, pick, head, uniq).

    Reads in a thread so a slow producer does not block the event loop.
    """
    loop = asyncio.get_running_loop()
    records: list[Record] = []

    def read() -> None:
        for item in source:
            if isinstance(item, InputError):
                if on_input_error is not None:
                    on_input_error(item)
            else:
                records.append(item)

    await loop.run_in_executor(None, read)
    return records
