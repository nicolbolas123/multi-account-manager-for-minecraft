"""Generate the app icon: packaging/AppIcon.icns (for the .app) and mcmanager/ui/assets/app-icon.png.

A dark rounded tile holding a 2x2 grid of small Minecraft-style "game windows" — one per
account — in the dashboard's colours. Drawn with Qt so no image editor is needed; rerun after
changing the design:  python packaging/make_icon.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ICNS = ROOT / "packaging" / "AppIcon.icns"
PNG = ROOT / "mcmanager" / "ui" / "assets" / "app-icon.png"

SKIES = [("#7ec8ff", "#bfe6ff"), ("#f6b26b", "#ffe2a8"), ("#8e7cf0", "#c9bdff"), ("#5fd3c4", "#b9f3ea")]


def draw(size: int = 1024) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 1024

    # macOS icon grid: 824 px rounded square centred in 1024, radius ~185.
    body = QRectF(100 * s, 100 * s, 824 * s, 824 * s)
    path = QPainterPath()
    path.addRoundedRect(body, 185 * s, 185 * s)
    grad = QLinearGradient(body.topLeft(), body.bottomLeft())
    grad.setColorAt(0, QColor("#232a3b"))
    grad.setColorAt(1, QColor("#0f1115"))
    p.fillPath(path, grad)
    p.setPen(QPen(QColor(91, 140, 255, 120), 6 * s))
    p.drawPath(path)

    # 2x2 grid of game windows
    gap, pad = 34 * s, 128 * s
    tile = (824 * s - 2 * pad + 100 * s - gap) / 2
    origin = 100 * s + pad - 50 * s
    for i, (sky_top, sky_bottom) in enumerate(SKIES):
        x = origin + (i % 2) * (tile + gap)
        y = origin + (i // 2) * (tile + gap)
        r = QRectF(x, y, tile, tile * 0.82)
        clip = QPainterPath()
        clip.addRoundedRect(r, 26 * s, 26 * s)
        p.save()
        p.setClipPath(clip)
        sky = QLinearGradient(r.topLeft(), r.bottomLeft())
        sky.setColorAt(0, QColor(sky_top))
        sky.setColorAt(1, QColor(sky_bottom))
        p.fillRect(r, sky)
        # blocky hills: grass on dirt, in "pixels"
        block = r.width() / 10
        heights = [5, 5, 4, 4, 3, 4, 5, 5, 4, 4] if i % 2 else [4, 4, 5, 5, 5, 4, 3, 3, 4, 5]
        for col, h in enumerate(heights):
            top = r.bottom() - block * (h - 1.6)
            p.fillRect(QRectF(r.left() + col * block, top, block + 1, r.bottom() - top), QColor("#8a5a35"))
            p.fillRect(QRectF(r.left() + col * block, top, block + 1, block * 0.9), QColor("#5fae3c"))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 40), 3 * s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(clip)

    # status dots under the tiles: green = running
    for i in range(4):
        x = origin + (i % 2) * (tile + gap) + tile / 2
        y = origin + (i // 2) * (tile + gap) + tile * 0.82 + 30 * s
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4ade80" if i != 2 else "#6b7282"))
        p.drawEllipse(QRectF(x - 11 * s, y - 11 * s, 22 * s, 22 * s))
    p.end()
    return img


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    big = draw(1024)
    PNG.parent.mkdir(parents=True, exist_ok=True)
    big.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(str(PNG))
    if shutil.which("iconutil") is None:
        print("iconutil not found (macOS only); wrote", PNG)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for pt in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = pt * scale
                name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
                img = big if px == 1024 else draw(px) if px <= 64 else big.scaled(
                    px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                img.save(str(iconset / name))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)
    print("wrote", ICNS, "and", PNG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
