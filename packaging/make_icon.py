"""Generate the app icon: packaging/AppIcon.icns (for the .app), packaging/AppIcon.ico (for the
Windows .exe) and mcmanager/ui/assets/app-icon.png.

A dark rounded tile holding a 2x2 grid of small Minecraft-style "game windows" — one per
account — in the dashboard's colours. Drawn with Qt so no image editor is needed; rerun after
changing the design:  python packaging/make_icon.py
Only the Windows icon:  python packaging/make_icon.py --ico
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ICNS = ROOT / "packaging" / "AppIcon.icns"
ICO = ROOT / "packaging" / "AppIcon.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
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


def draw_windows(size: int) -> QImage:
    """The same design with less margin: Windows icons fill more of their square than macOS's."""
    full = round(size * 1024 / 872)  # the tile spans 76..948 of 1024 after cropping
    margin = round(76 * full / 1024)
    return draw(full).copy(margin, margin, size, size)


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def dib_bytes(img: QImage) -> bytes:
    """An uncompressed 32-bit icon image: BITMAPINFOHEADER, bottom-up BGRA pixels, AND mask."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)  # little-endian memory order: B, G, R, A
    w, h = img.width(), img.height()
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)  # height covers XOR + AND
    pixels = b"".join(bytes(img.constScanLine(y))[: w * 4] for y in reversed(range(h)))
    mask_row = ((w + 31) // 32) * 4  # 1 bit per pixel, rows padded to 32 bits; all 0 = use alpha
    return header + pixels + bytes(mask_row * h)


def write_ico(path: Path, images: list[QImage]) -> None:
    """A multi-size .ico: uncompressed bitmaps below 256 px, PNG only for 256 px.

    That is the layout Windows' own icons use. PNG-compressed small entries are valid since Vista
    but some shell code paths (the taskbar among them) show a blank icon for them."""
    blobs = [png_bytes(img) if img.width() >= 256 else dib_bytes(img) for img in images]
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))  # reserved, type 1 = icon, count
    offset = 6 + 16 * len(images)
    for img, blob in zip(images, blobs):
        w, h = img.width(), img.height()
        out.write(struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    for blob in blobs:
        out.write(blob)
    path.write_bytes(out.getvalue())


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    write_ico(ICO, [draw_windows(px) for px in ICO_SIZES])
    if "--ico" in sys.argv[1:]:
        print("wrote", ICO)
        return 0
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
