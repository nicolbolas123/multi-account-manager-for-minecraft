"""Sequential launching of several accounts.

Every multi-account launch (Launch All, Launch Group, Launch Selected, restarts
of several accounts) goes through this queue. It never blocks: a Qt timer
ticks a few times per second and decides whether the next launch may start.

Two modes:

* **Staggered Launch off** (the original behaviour): launch attempts are
  spaced ``DEFAULT_SPACING_S`` apart, without waiting for anything else.
* **Staggered Launch on**: the next attempt starts only when *both* the
  configured launch delay has passed since the previous attempt *and* the
  previous instance has left the "Launching" state (its game process appeared,
  or the launch failed). A fixed delay alone does not mean an instance has
  finished loading, so the process check is used as well; the manager's launch
  timeout guarantees a stuck launch turns into "failed", so the queue can't
  hang forever.

Failures (Prism errors, "already running", missing instance) are recorded on
the item and the queue moves on. Items that have not started yet can be
cancelled.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from mcmanager.runtime.models import RunState

log = logging.getLogger(__name__)

DEFAULT_SPACING_S = 2.5
TICK_MS = 250
_batch_ids = itertools.count(1)


class ItemState(Enum):
    WAITING = "Waiting"
    LAUNCHING = "Launching"
    STARTED = "Started"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"

    @property
    def pending(self) -> bool:
        return self in (ItemState.WAITING, ItemState.LAUNCHING)


@dataclass
class QueueItem:
    entry_id: str
    state: ItemState = ItemState.WAITING
    message: str = ""
    attempted_at: float | None = None  # monotonic time of a successful launch request


@dataclass
class LaunchBatch:
    items: list[QueueItem] = field(default_factory=list)
    staggered: bool = False
    finished: bool = False
    batch_id: int = field(default_factory=lambda: next(_batch_ids))

    def count(self, *states: ItemState) -> int:
        return sum(1 for i in self.items if i.state in states)

    @property
    def is_bulk(self) -> bool:
        return len(self.items) > 1


class LaunchQueue(QObject):
    changed = Signal()
    batch_finished = Signal(object)  # LaunchBatch

    def __init__(self, manager, clock: Callable[[], float] = time.monotonic, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.clock = clock
        self.batch: LaunchBatch | None = None
        self._timer = QTimer(self, interval=TICK_MS, timeout=self.tick)

    # ------------------------------------------------------------------ queries
    @property
    def active(self) -> bool:
        return self.batch is not None and not self.batch.finished

    def item(self, entry_id: str) -> QueueItem | None:
        if not self.active:
            return None
        return next((i for i in self.batch.items if i.entry_id == entry_id), None)

    def is_waiting(self, entry_id: str) -> bool:
        item = self.item(entry_id)
        return item is not None and item.state == ItemState.WAITING

    def waiting(self) -> list[QueueItem]:
        return [i for i in self.batch.items if i.state == ItemState.WAITING] if self.active else []

    def position(self, entry_id: str) -> int | None:
        """1-based place among the items still waiting."""
        ids = [i.entry_id for i in self.waiting()]
        return ids.index(entry_id) + 1 if entry_id in ids else None

    def delay_s(self) -> float:
        if self.batch and self.batch.staggered:
            return float(self.manager.settings.launch_delay_s)
        return DEFAULT_SPACING_S

    def seconds_until_next(self) -> float | None:
        """Time until the delay allows the next attempt (None if nothing waits)."""
        if not self.waiting():
            return None
        last = self._last_attempt()
        if last is None:
            return 0.0
        return max(0.0, self.delay_s() - (self.clock() - last.attempted_at))

    def blocked_by(self) -> QueueItem | None:
        """In staggered mode, the earlier item the queue is waiting on to finish launching."""
        last = self._last_attempt()
        if self.batch and self.batch.staggered and last and last.state == ItemState.LAUNCHING:
            return last
        return None

    # ----------------------------------------------------------------- commands
    def enqueue(self, entry_ids: list[str], staggered: bool) -> int:
        """Queue launches; returns how many were added. Duplicates and active games are skipped."""
        if not self.active:
            self.batch = LaunchBatch(staggered=staggered)
        added = 0
        for entry_id in entry_ids:
            if self.item(entry_id) and self.item(entry_id).state.pending:
                continue  # already queued or launching in this batch
            existing = self.item(entry_id)
            if existing:  # finished earlier in this batch (e.g. failed) and requested again
                self.batch.items.remove(existing)
            if self.manager.info(entry_id).state.is_active:
                continue
            self.batch.items.append(QueueItem(entry_id))
            added += 1
        if added:
            self._timer.start()
            self.tick()
        elif self.batch and not self.batch.items:
            self.batch = None
        self.changed.emit()
        return added

    def cancel(self, entry_id: str, reason: str = "Cancelled") -> bool:
        item = self.item(entry_id)
        if item is None or item.state != ItemState.WAITING:
            return False
        item.state, item.message = ItemState.CANCELLED, reason
        self.manager.entry_updated.emit(entry_id)
        self._maybe_finish()
        self.changed.emit()
        return True

    def cancel_waiting(self) -> int:
        ids = [i.entry_id for i in self.waiting()]
        for entry_id in ids:
            self.cancel(entry_id)
        return len(ids)

    # --------------------------------------------------------------------- loop
    def _last_attempt(self) -> QueueItem | None:
        attempted = [i for i in self.batch.items if i.attempted_at is not None] if self.batch else []
        return max(attempted, key=lambda i: i.attempted_at) if attempted else None

    def tick(self) -> None:
        if not self.active:
            self._timer.stop()
            return
        changed = self._resolve_launching()
        # Launch as many as allowed right now (immediate failures don't use up the delay).
        while self._may_launch_next():
            self._launch(self.waiting()[0])
            changed = True
        if self._maybe_finish() or changed:
            self.changed.emit()

    def _resolve_launching(self) -> bool:
        changed = False
        for item in self.batch.items:
            if item.state != ItemState.LAUNCHING:
                continue
            info = self.manager.info(item.entry_id)
            if info.state in (RunState.RUNNING, RunState.STOPPING, RunState.EXITED, RunState.CRASHED):
                item.state, item.message = ItemState.STARTED, ""
                changed = True
            elif info.state in (RunState.FAILED, RunState.STOPPED):
                item.state, item.message = ItemState.FAILED, info.message or "Launch failed"
                changed = True
        return changed

    def _may_launch_next(self) -> bool:
        if not self.waiting():
            return False
        if self.blocked_by() is not None:
            return False
        return self.seconds_until_next() == 0.0

    def _launch(self, item: QueueItem) -> None:
        ok, reason = self.manager.can_launch(item.entry_id)
        if not ok:
            already = self.manager.info(item.entry_id).state.is_active
            item.state = ItemState.SKIPPED if already else ItemState.FAILED
            item.message = reason
            log.info("Queue: %s %s (%s)", item.entry_id, item.state.value.lower(), reason)
            if not already:
                self.manager.report_launch_failure(item.entry_id, reason, shown=False)
            return
        item.state = ItemState.LAUNCHING  # before launch(): it must not see this item as waiting
        if self.manager.launch(item.entry_id, from_queue=True):
            item.attempted_at = self.clock()
        else:
            item.state = ItemState.FAILED
            item.message = self.manager.info(item.entry_id).message or "Launch failed"

    def _maybe_finish(self) -> bool:
        if not self.active or any(i.state.pending for i in self.batch.items):
            return False
        self.batch.finished = True
        self._timer.stop()
        b = self.batch
        log.info("Launch queue finished: %d started, %d failed, %d skipped, %d cancelled",
                 b.count(ItemState.STARTED), b.count(ItemState.FAILED),
                 b.count(ItemState.SKIPPED), b.count(ItemState.CANCELLED))
        self.batch_finished.emit(b)
        return True
