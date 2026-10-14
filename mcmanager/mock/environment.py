"""Mock environment for ``--mock`` mode: fake Prism data, launcher, windows and capture.

Everything here is clearly fake and isolated from real data:
* the fake Prism data directory lives in the app's Caches folder,
* mock settings are saved to a separate file,
* previews are synthetic images labelled "MOCK PREVIEW".
The process tracking, log parsing and crash detection that run against the
fake clients are the real implementations.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

from mcmanager.bundle import fake_client_command
from mcmanager.prism import LaunchRequest, PrismInstallation
from mcmanager.windows.capture import CaptureBackend, CaptureError, target_size
from mcmanager.windows.discovery import WindowInfo
from mcmanager.windows.focus import ActionResult

MOCK_INSTANCES = {
    "Mock Survival": ("1.21.4", "Fabric"),
    "Mock Skyblock": ("1.8.9", "Forge"),
    "Mock Creative": ("26.2", None),
    "Mock Crashy": ("1.20.1", "NeoForge"),  # crashes ~20 s after launch to demo crash detection
}
MOCK_ACCOUNTS = ["MockPlayer1", "MockPlayer2", "MockBuilder"]
FAKE_CLIENT_MODULE = "mcmanager.mock.fake_client"
FAKE_CLIENT_MARKERS = (FAKE_CLIENT_MODULE, "--mcmanager-fake-client")  # source / packaged command lines


def create_mock_prism(root: Path) -> PrismInstallation:
    instances = root / "instances"
    for name, (version, loader) in MOCK_INSTANCES.items():
        inst = instances / name
        (inst / "minecraft" / "logs").mkdir(parents=True, exist_ok=True)
        (inst / "natives").mkdir(exist_ok=True)
        (inst / "instance.cfg").write_text(f"[General]\nInstanceType=OneSix\nname={name}\n")
        components = [{"uid": "net.minecraft", "version": version}]
        if loader == "Fabric":
            components.append({"uid": "net.fabricmc.fabric-loader", "version": "0.16.0"})
        elif loader == "Forge":
            components.append({"uid": "net.minecraftforge", "version": "11.15.1"})
        elif loader == "NeoForge":
            components.append({"uid": "net.neoforged", "version": "47.1.0"})
        (inst / "mmc-pack.json").write_text(json.dumps({"components": components, "formatVersion": 1}))
    # Profile names only - the mock never contains credentials.
    accounts = [{"type": "Offline", "profile": {"name": n}, "active": i == 0} for i, n in enumerate(MOCK_ACCOUNTS)]
    (root / "accounts.json").write_text(json.dumps({"formatVersion": 3, "accounts": accounts}))
    return PrismInstallation(
        executable=None, app_bundle=None, data_dir=root, instances_dir=instances, version="mock",
    )


class MockLaunchBackend:
    is_mock = True

    def launch(self, install: PrismInstallation, request: LaunchRequest) -> subprocess.Popen:
        inst = install.instances_dir / request.instance_id
        base, env = fake_client_command()
        cmd = base + [
            f"-Djava.library.path={inst / 'natives'}",
            "--instance-dir", str(inst),
            "--player", request.profile or MOCK_ACCOUNTS[0],
        ]
        if request.instance_id == "Mock Crashy":
            cmd += ["--crash-after", "20"]
        return subprocess.Popen(cmd, cwd=inst / "minecraft", env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def show(self, install: PrismInstallation, instance_id: str) -> None:
        pass  # nothing to show in mock mode


class MockWindowService:
    """Pretends every fake client has a 16:9 window whose id equals its pid."""

    is_mock = True

    def find_windows(self, pids: set[int]) -> dict[int, WindowInfo]:
        windows = {}
        for pid in pids:
            try:
                if any(marker in " ".join(psutil.Process(pid).cmdline()) for marker in FAKE_CLIENT_MARKERS):
                    windows[pid] = WindowInfo(window_id=pid, pid=pid, width=1280, height=720)
            except psutil.Error:
                continue
        return windows

    def bring_to_front(self, pid: int) -> ActionResult:
        return ActionResult(True, "Mock mode: there is no real game window to bring forward.")

    def close_gracefully(self, pid: int) -> ActionResult:
        return ActionResult(False, "mock windows cannot be closed")


class MockCaptureBackend(CaptureBackend):
    """Renders an animated, clearly-labelled synthetic frame."""

    name = "mock"

    def capture(self, window: WindowInfo, max_width: int) -> QImage:
        if not psutil.pid_exists(window.pid):
            raise CaptureError("Window closed")
        w, h = target_size(window.width, window.height, max_width)
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        t = time.time()
        hue = (window.pid * 37) % 360
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0, QColor.fromHsv(hue, 120, 200))
        sky.setColorAt(1, QColor.fromHsv((hue + 40) % 360, 80, 90))
        p.fillRect(img.rect(), sky)
        block = max(6, w // 24)
        offset = int(t * block * 0.8) % (block * 2)
        for i in range(-2, w // block + 3):
            col_h = int((math.sin((i * block + offset) / (block * 3)) + 2.2) * h / 10)
            x = i * block - offset
            p.fillRect(QRectF(x, h - col_h, block, col_h), QColor(86, 125, 70))
            p.fillRect(QRectF(x, h - col_h, block, block / 3), QColor(110, 160, 80))
        sun_x = (t * 20 + window.pid) % (w + 60) - 30
        p.setBrush(QColor(255, 240, 160))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(sun_x, h * 0.2), h * 0.06, h * 0.06)
        p.setPen(QColor(255, 255, 255, 230))
        font = QFont()
        font.setPixelSize(max(10, h // 9))
        font.setBold(True)
        p.setFont(font)
        p.drawText(img.rect().adjusted(0, h // 6, 0, 0), Qt.AlignmentFlag.AlignHCenter, "MOCK PREVIEW")
        font.setPixelSize(max(8, h // 18))
        font.setBold(False)
        p.setFont(font)
        p.drawText(img.rect().adjusted(0, h // 6 + h // 7, 0, 0), Qt.AlignmentFlag.AlignHCenter,
                   f"fake client pid {window.pid} · {time.strftime('%H:%M:%S')}")
        p.end()
        return img
