"""Bars shown above the grid: the bulk-actions toolbar and launch-queue progress."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from mcmanager.runtime.launch_queue import ItemState, LaunchQueue
from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.ui.widgets import button


def _relayout(bar: QFrame) -> None:
    """Resize the bar's labels/buttons for their new text now, not on a later layout pass."""
    bar.layout().invalidate()
    bar.layout().activate()


class BulkBar(QFrame):
    """Shown while one or more tiles are selected."""

    requested = Signal(str)  # "launch" | "stop" | "restart" | "deselect"

    def __init__(self):
        super().__init__()
        self.setObjectName("ActionBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("ActionBarTitle")
        row.addWidget(self.title)
        self.detail = QLabel()
        self.detail.setObjectName("ActionBarText")
        row.addWidget(self.detail, 1)
        self.launch_btn = button("Launch Selected", "primary")
        self.stop_btn = button("Stop Selected", "danger")
        self.restart_btn = button("Restart Selected")
        self.clear_btn = button("Deselect All", "ghost", "Clear the selection (Esc)")
        for b, action in ((self.launch_btn, "launch"), (self.stop_btn, "stop"),
                          (self.restart_btn, "restart"), (self.clear_btn, "deselect")):
            b.clicked.connect(lambda _=False, a=action: self.requested.emit(a))
            row.addWidget(b)
        self.hide()

    def update_for(self, manager: InstanceManager, ids: list[str]) -> None:
        self.setVisible(bool(ids))
        if not ids:
            return
        names = [manager.entry(i).display_name for i in ids if manager.entry(i)]
        launchable = [i for i in ids if manager.can_launch(i)[0] and not manager.queue.is_waiting(i)]
        running = [i for i in ids if manager.info(i).state == RunState.RUNNING]
        self.title.setText(f"{len(ids)} selected")
        self.title.setToolTip("\n".join(names))
        bits = []
        if running:
            bits.append(f"{len(running)} running")
        if launchable:
            bits.append(f"{len(launchable)} can launch")
        self.detail.setText("  ·  ".join(bits))
        self.detail.setToolTip("\n".join(names))
        self.launch_btn.setEnabled(bool(launchable))
        self.launch_btn.setText(f"Launch Selected ({len(launchable)})" if launchable else "Launch Selected")
        self.stop_btn.setEnabled(bool(running))
        self.stop_btn.setText(f"Stop Selected ({len(running)})" if running else "Stop Selected")
        self.restart_btn.setEnabled(bool(running))
        _relayout(self)


class QueueBar(QFrame):
    """Progress of the launch queue, with a way to cancel launches that have not started."""

    cancel_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("ActionBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("ActionBarTitle")
        row.addWidget(self.title)
        self.detail = QLabel()
        self.detail.setObjectName("ActionBarText")
        self.detail.setWordWrap(True)
        row.addWidget(self.detail, 1)
        self.cancel_btn = button("Cancel Waiting Launches", None, "Launches already in progress are not affected")
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_btn)
        self.hide()

    def update_for(self, manager: InstanceManager, queue: LaunchQueue) -> None:
        if not queue.active:
            self.hide()
            return
        batch = queue.batch
        name = lambda i: manager.entry(i.entry_id).display_name if manager.entry(i.entry_id) else "?"  # noqa: E731
        total = len(batch.items)
        done = batch.count(ItemState.STARTED)
        failed = batch.count(ItemState.FAILED)
        mode = "Staggered launch" if batch.staggered else "Launching"
        self.title.setText(f"{mode}: {done} of {total} started" + (f", {failed} failed" if failed else ""))
        launching = [name(i) for i in batch.items if i.state == ItemState.LAUNCHING]
        waiting = queue.waiting()
        parts = []
        if launching:
            parts.append("Launching: " + ", ".join(launching))
        if waiting:
            blocker = queue.blocked_by()
            secs = queue.seconds_until_next()
            if blocker is not None:
                when = f"after {name(blocker)} starts"
                if secs:
                    when += f" (and ≥ {secs:.0f} s)"
            else:
                when = f"in {secs:.0f} s" if secs else "now"
            parts.append(f"Next: {name(waiting[0])} {when}")
            if len(waiting) > 1:
                parts.append("Waiting: " + ", ".join(name(i) for i in waiting[1:]))
        self.detail.setText("  ·  ".join(parts))
        self.cancel_btn.setEnabled(bool(waiting))
        self.show()
        _relayout(self)
