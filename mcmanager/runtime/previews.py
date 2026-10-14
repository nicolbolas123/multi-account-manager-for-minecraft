"""Decides which windows are captured, how often and how large.

Grid tiles are captured at a low rate and size; the expanded (focused) entry
gets a higher rate and resolution while the rest drop to a background rate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from mcmanager.runtime.manager import InstanceManager
from mcmanager.runtime.models import RunState
from mcmanager.windows.capture import CaptureTarget, CaptureWorker, create_backend

BACKGROUND_FPS = 1.0


@dataclass
class PreviewState:
    frame: QImage | None = None
    frame_time: float = 0.0
    error: str | None = None
    permission_problem: bool = False

    @property
    def stale(self) -> bool:
        return self.frame is not None and time.monotonic() - self.frame_time > 3.0


class PreviewCoordinator(QObject):
    preview_updated = Signal(str)
    permission_problem_changed = Signal(bool)

    def __init__(self, manager: InstanceManager, worker: CaptureWorker):
        super().__init__()
        self.manager = manager
        self.worker = worker
        self.states: dict[str, PreviewState] = {}
        self.focused: str | None = None
        self.paused = False
        self._permission_problem = False
        worker.frame_ready.connect(self._on_frame)
        worker.capture_failed.connect(self._on_failure)
        manager.entry_updated.connect(lambda _id: self.refresh_targets())
        manager.entries_changed.connect(self.refresh_targets)

    @property
    def permission_problem(self) -> bool:
        return self._permission_problem

    def state(self, entry_id: str) -> PreviewState:
        return self.states.setdefault(entry_id, PreviewState())

    def set_focused(self, entry_id: str | None) -> None:
        self.focused = entry_id
        self.refresh_targets()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.refresh_targets()

    def apply_backend_setting(self) -> None:
        self.worker.set_backend(create_backend(self.manager.settings.preview.backend))
        self._set_permission_problem(False)

    def refresh_targets(self) -> None:
        prefs = self.manager.settings.preview
        targets: dict[str, CaptureTarget] = {}
        for entry in self.manager.entries:
            info = self.manager.info(entry.id)
            active = info.state in (RunState.RUNNING, RunState.STOPPING)
            if not active and entry.id in self.states and self.states[entry.id].frame is not None:
                self.states[entry.id] = PreviewState()
                self.preview_updated.emit(entry.id)
            if self.paused or not active or info.window is None:
                continue
            if self.focused == entry.id:
                fps, width = prefs.expanded_fps, prefs.expanded_max_width
            elif self.focused is not None:
                fps, width = BACKGROUND_FPS, prefs.grid_max_width
            else:
                fps, width = prefs.grid_fps, prefs.grid_max_width
            targets[entry.id] = CaptureTarget(info.window, fps, width)
        self.worker.set_targets(targets)

    def _on_frame(self, entry_id: str, image: QImage) -> None:
        st = self.state(entry_id)
        st.frame, st.frame_time, st.error, st.permission_problem = image, time.monotonic(), None, False
        self._set_permission_problem(False)
        self.preview_updated.emit(entry_id)

    def _on_failure(self, entry_id: str, message: str, permission: bool) -> None:
        st = self.state(entry_id)
        changed = st.error != message
        st.error, st.permission_problem = message, permission
        if permission:
            self._set_permission_problem(True)
        if changed:
            self.preview_updated.emit(entry_id)

    def _set_permission_problem(self, value: bool) -> None:
        if value != self._permission_problem:
            self._permission_problem = value
            self.permission_problem_changed.emit(value)
