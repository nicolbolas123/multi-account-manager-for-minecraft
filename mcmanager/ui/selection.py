"""Multi-selection of dashboard tiles with familiar desktop rules.

* plain click     – select only the clicked tile (it becomes the anchor)
* toggle click    – add/remove the clicked tile, keep the rest (it becomes the anchor)
* shift click     – select the range anchor…clicked (inclusive, either direction);
                    the anchor stays, so repeated shift-clicks re-size the range
* toggle + shift  – add that range to the existing selection

The toggle modifier is Command (⌘) on macOS and Control elsewhere. Qt already
maps this: on macOS ``Qt.ControlModifier`` *is* the Command key (the physical
Control key is ``MetaModifier`` and opens context menus), so checking
``ControlModifier`` gives the platform-conventional behaviour everywhere.

Ranges follow the on-screen order (groups top to bottom, tiles in reading
order), passed in by the caller as ``order``. Selection is purely visual state:
it never launches, stops or expands anything.
"""

from __future__ import annotations

from PySide6.QtCore import Qt


class Selection:
    def __init__(self):
        self._ids: list[str] = []  # insertion-ordered
        self.anchor: str | None = None
        self._base: list[str] = []  # selection before the current shift-range was applied

    # ---------------------------------------------------------------- queries
    @property
    def ids(self) -> list[str]:
        return list(self._ids)

    def __contains__(self, entry_id: str) -> bool:
        return entry_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def ordered(self, order: list[str]) -> list[str]:
        """Selected ids in display order (selected-but-hidden ones last)."""
        pos = {e: i for i, e in enumerate(order)}
        return sorted(self._ids, key=lambda e: pos.get(e, len(pos)))

    # ---------------------------------------------------------------- changes
    def click(self, entry_id: str, modifiers, order: list[str]) -> None:
        toggle = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if shift and self.anchor in order and entry_id in order:
            a, b = order.index(self.anchor), order.index(entry_id)
            span = order[min(a, b):max(a, b) + 1]
            base = self._base if toggle else []
            self._ids = base + [e for e in span if e not in base]
            return
        if toggle:
            if entry_id in self._ids:
                self._ids.remove(entry_id)
            else:
                self._ids.append(entry_id)
        else:
            self._ids = [entry_id]
        self.anchor = entry_id
        self._base = list(self._ids)

    def set(self, ids: list[str], anchor: str | None = None) -> None:
        self._ids = list(dict.fromkeys(ids))
        self.anchor = anchor if anchor in self._ids else (self._ids[-1] if self._ids else None)
        self._base = list(self._ids)

    def clear(self) -> None:
        self.set([])

    def prune(self, valid) -> bool:
        """Drop ids that no longer exist. Returns True if anything changed."""
        kept = [e for e in self._ids if e in valid]
        if kept == self._ids:
            return False
        self.set(kept, self.anchor)
        return True
