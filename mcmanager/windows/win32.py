"""Windows implementation of window discovery, focus, graceful close and live capture.

Uses the Win32 API through ``ctypes`` (user32, gdi32, dwmapi): no extra packages, no
administrator rights. A normal process may enumerate, capture, focus and close the windows of
other processes of the same user; elevated (admin) games are the exception (see README).

Discovery
    ``EnumWindows`` + ``GetWindowThreadProcessId``. Of each game process's top-level windows,
    hidden, owned (dialogs), tool and cloaked (other virtual desktop) windows are skipped and the
    largest remaining one is taken as the game window (Minecraft has one GLFW window).
Focus
    ``ShowWindow(SW_RESTORE)`` for minimized windows, then ``SetForegroundWindow``. Windows only
    lets the foreground app hand over focus; the dashboard is foreground when its button is
    clicked, and the ``AttachThreadInput`` fallback covers the rest.
Graceful close
    ``WM_CLOSE`` to the game window: the same as clicking its ✕, so Minecraft saves and exits.
Capture (``Win32CaptureBackend``)
    ``"printwindow"`` (default): ``PrintWindow`` with ``PW_RENDERFULLCONTENT`` asks DWM for the
    window's own content, so it works while the window is covered by other windows. Some
    OpenGL/GPU driver combinations return a black image; that is reported, not shown.
    ``"screen"``: copies the window's area from the screen. Works for any renderer, but only shows
    the game while it's visible and not covered.
    Minimized windows can't be captured (Windows doesn't render them); the last frame is kept.

Everything Win32 is loaded lazily, so this module imports (and its pure helpers can be
tested) on any platform.
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache

from mcmanager.windows.capture import CaptureBackend
from mcmanager.windows.discovery import WindowInfo, choose_window

log = logging.getLogger(__name__)

# Constants (WinUser.h / dwmapi.h / wingdi.h)
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010
WM_GETICON = 0x007F
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
GCLP_HICON, GCLP_HICONSM = -14, -34
SM_CXICON, SM_CXSMICON = 11, 49
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0

CAPTURE_METHODS = {
    "printwindow": "Window content (PrintWindow) — works while covered",
    "screen": "Screen copy — window must be visible",
}


@lru_cache(maxsize=1)
def _api():
    """Load user32/gdi32/dwmapi with exact signatures (handles are pointer-sized on 64-bit)."""
    if sys.platform != "win32":
        raise OSError("The Win32 API is only available on Windows")
    import ctypes
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None

    def sig(fn, restype, *argtypes):
        fn.restype, fn.argtypes = restype, list(argtypes)
        return fn

    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    sig(user32.EnumWindows, wt.BOOL, enum_proc, wt.LPARAM)
    sig(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
    sig(user32.IsWindow, wt.BOOL, wt.HWND)
    sig(user32.IsWindowVisible, wt.BOOL, wt.HWND)
    sig(user32.IsIconic, wt.BOOL, wt.HWND)
    sig(user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
    sig(user32.GetWindowLongPtrW, ctypes.c_ssize_t, wt.HWND, ctypes.c_int)
    sig(user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
    sig(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    sig(user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
    sig(user32.GetClientRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
    sig(user32.ClientToScreen, wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
    sig(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
    sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
    sig(user32.GetForegroundWindow, wt.HWND)
    sig(user32.BringWindowToTop, wt.BOOL, wt.HWND)
    sig(user32.AttachThreadInput, wt.BOOL, wt.DWORD, wt.DWORD, wt.BOOL)
    sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    sig(user32.PrintWindow, wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
    sig(user32.SendMessageW, ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    sig(user32.SetClassLongPtrW, ctypes.c_size_t, wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
    sig(user32.LoadImageW, wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
    sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    try:  # Windows 10 1607+: icon sizes for the window's monitor DPI
        sig(user32.GetDpiForWindow, wt.UINT, wt.HWND)
        sig(user32.GetSystemMetricsForDpi, ctypes.c_int, ctypes.c_int, wt.UINT)
    except AttributeError:
        pass
    sig(user32.GetDC, wt.HDC, wt.HWND)
    sig(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
    sig(kernel32.GetCurrentThreadId, wt.DWORD)
    sig(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
    sig(gdi32.CreateDIBSection, wt.HBITMAP, wt.HDC, ctypes.c_void_p, wt.UINT,
        ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
    sig(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
    sig(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
    sig(gdi32.DeleteDC, wt.BOOL, wt.HDC)
    sig(gdi32.BitBlt, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)
    sig(gdi32.GdiFlush, wt.BOOL)
    if dwmapi is not None:
        sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)

    class Api:
        pass

    api = Api()
    api.ctypes, api.wt = ctypes, wt
    api.user32, api.gdi32, api.kernel32, api.dwmapi = user32, gdi32, kernel32, dwmapi
    api.enum_proc = enum_proc
    return api


# ---------------------------------------------------------------- discovery

def _window_pid(api, hwnd) -> int:
    pid = api.wt.DWORD(0)
    api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(pid))
    return int(pid.value)


def _is_cloaked(api, hwnd) -> bool:
    if api.dwmapi is None:
        return False
    cloaked = api.wt.DWORD(0)
    res = api.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, api.ctypes.byref(cloaked),
                                           api.ctypes.sizeof(cloaked))
    return res == 0 and cloaked.value != 0


def _client_size(api, hwnd) -> tuple[int, int]:
    rect = api.wt.RECT()
    if not api.user32.GetClientRect(hwnd, api.ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def _window_title(api, hwnd) -> str | None:
    n = api.user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return None
    buf = api.ctypes.create_unicode_buffer(n + 1)
    api.user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or None


def find_windows(pids: set[int]) -> dict[int, WindowInfo]:
    """Return the most likely main window for each PID that has one."""
    if sys.platform != "win32" or not pids:
        return {}
    api = _api()
    by_pid: dict[int, list[WindowInfo]] = {}

    def visit(hwnd, _lparam):
        try:
            pid = _window_pid(api, hwnd)
            if pid not in pids or not api.user32.IsWindowVisible(hwnd):
                return True
            if api.user32.GetWindow(hwnd, GW_OWNER):
                return True  # dialogs and other owned windows
            if api.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(api, hwnd):
                return True
            minimized = bool(api.user32.IsIconic(hwnd))
            width, height = _client_size(api, hwnd)
            rect = api.wt.RECT()
            api.user32.GetWindowRect(hwnd, api.ctypes.byref(rect))
            if minimized:  # a minimized window reports a tiny client area; keep it as "hidden"
                width, height = max(width, 1280), max(height, 720)
            by_pid.setdefault(pid, []).append(WindowInfo(
                window_id=int(hwnd), pid=pid, width=width, height=height,
                x=rect.left, y=rect.top, onscreen=not minimized, title=_window_title(api, hwnd)))
        except Exception:  # never let one odd window stop the enumeration
            log.debug("Skipping window %s", hwnd, exc_info=True)
        return True

    callback = api.enum_proc(visit)  # keep a reference for the duration of the call
    api.user32.EnumWindows(callback, 0)
    result = {}
    for pid, windows in by_pid.items():
        chosen = choose_window(windows)
        if chosen:
            result[pid] = chosen
    return result


def _main_window(pid: int) -> int | None:
    info = find_windows({pid}).get(pid)
    return info.window_id if info else None


# ---------------------------------------------------------------- focus / close

def bring_to_front(pid: int):
    from mcmanager.windows.focus import ActionResult

    hwnd = _main_window(pid)
    if hwnd is None:
        return ActionResult(False, "The game has no window yet (or it is still loading).")
    api = _api()
    user32 = api.user32
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    if user32.SetForegroundWindow(hwnd):
        return ActionResult(True)
    # Windows refuses focus changes from background apps; borrowing the foreground thread's
    # input state is the documented-by-practice workaround and needs no special rights.
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = api.kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    if ok:
        return ActionResult(True)
    return ActionResult(False, "Windows didn't allow switching to the game window; its taskbar button "
                               "should be flashing. Click it to switch.")


def close_window_gracefully(pid: int):
    """Send WM_CLOSE to the game window (like clicking its ✕), so Minecraft saves and quits."""
    from mcmanager.windows.focus import ActionResult

    hwnd = _main_window(pid)
    if hwnd is None:
        return ActionResult(False, "No game window")
    if not _api().user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        return ActionResult(False, f"PostMessage failed (error {_api().ctypes.get_last_error()})")
    return ActionResult(True)


# ---------------------------------------------------------------- taskbar

def set_app_user_model_id(app_id: str) -> bool:
    """Give this process its own taskbar identity (and so its own taskbar icon).

    Only for running from source: there the process is python.exe, whose identity and icon the
    taskbar would otherwise use. The packaged .exe keeps the identity Windows derives from its
    path, so pinning it to the taskbar keeps working."""
    import ctypes

    try:
        shell32 = ctypes.WinDLL("shell32")
        fn = shell32.SetCurrentProcessExplicitAppUserModelID
        fn.restype, fn.argtypes = ctypes.c_long, [ctypes.c_wchar_p]
        return fn(app_id) == 0
    except (OSError, AttributeError):
        return False


_icon_handles: dict[tuple[str, int], int] = {}  # loaded once and kept: the taskbar may use them any time


def _load_icon(api, path: str, size: int) -> int:
    key = (path, size)
    if key not in _icon_handles:
        _icon_handles[key] = api.user32.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE) or 0
    return _icon_handles[key]


def _icon_sizes(api, hwnd: int) -> tuple[int, int]:
    user32 = api.user32
    try:
        dpi = user32.GetDpiForWindow(hwnd)
        if dpi:
            return user32.GetSystemMetricsForDpi(SM_CXICON, dpi), user32.GetSystemMetricsForDpi(SM_CXSMICON, dpi)
    except AttributeError:
        pass
    return user32.GetSystemMetrics(SM_CXICON) or 32, user32.GetSystemMetrics(SM_CXSMICON) or 16


def set_window_icon(hwnd: int, ico_path: str) -> bool:
    """Set ``hwnd``'s taskbar/title-bar icon from an .ico file, robustly.

    The taskbar asks a new window for its icon (WM_GETICON) with a short timeout. If the app is
    still busy starting, the request times out and the taskbar falls back to the window *class*
    icon, which Qt leaves empty (it only looks for an ``IDI_ICON1`` resource): a blank taskbar
    button that stays blank. So the class gets the icon as well, and setting the window icon again
    (WM_SETICON) makes the taskbar refresh its button. Call it once the app is idle."""
    api = _api()
    big_size, small_size = _icon_sizes(api, hwnd)
    big, small = _load_icon(api, ico_path, big_size), _load_icon(api, ico_path, small_size)
    if not big or not small:
        log.warning("Could not load the window icon from %s (error %s)", ico_path, api.ctypes.get_last_error())
        return False
    user32 = api.user32
    user32.SetClassLongPtrW(hwnd, GCLP_HICON, big)
    user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, small)
    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
    return True


def window_icons(hwnd: int) -> tuple[bool, bool]:
    """Whether a window has a (small, big) icon set: the taskbar and title bar show these."""
    send = _api().user32.SendMessageW
    return bool(send(hwnd, WM_GETICON, ICON_SMALL, 0)), bool(send(hwnd, WM_GETICON, ICON_BIG, 0))


# ---------------------------------------------------------------- capture

def is_mostly_black(pixels: bytes | bytearray, step: int = 4 * 61) -> bool:
    """True if a sample of BGRA pixels has no visible colour (a failed capture looks like that)."""
    sample = pixels[0::step] + pixels[1::step] + pixels[2::step]
    return not sample or max(sample) < 8


def bgra_to_qimage(pixels: bytearray, width: int, height: int, max_width: int):
    """Wrap a top-down 32-bit BGRA bitmap as an opaque QImage, scaled to at most ``max_width``."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    from mcmanager.windows.capture import target_size

    pixels[3::4] = b"\xff" * (len(pixels) // 4)  # GDI leaves alpha undefined
    image = QImage(bytes(pixels), width, height, width * 4, QImage.Format.Format_RGB32).copy()
    w, h = target_size(width, height, max_width)
    if (w, h) != (width, height):
        image = image.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


class Win32CaptureBackend(CaptureBackend):
    """See the module docstring. ``method`` is ``"printwindow"`` or ``"screen"``."""

    def __init__(self, method: str = "printwindow"):
        self.method = method if method in CAPTURE_METHODS else "printwindow"
        self.name = f"win32-{self.method}"

    def capture(self, window: WindowInfo, max_width: int):
        from mcmanager.windows.capture import CaptureError

        api = _api()
        user32, gdi32, ctypes, wt = api.user32, api.gdi32, api.ctypes, api.wt
        hwnd = wt.HWND(window.window_id)
        if not user32.IsWindow(hwnd):
            raise CaptureError("Window closed")
        if user32.IsIconic(hwnd):
            raise CaptureError("Window is minimized")
        width, height = _client_size(api, hwnd)
        if width < 2 or height < 2:
            raise CaptureError("Window has no visible area")

        header = _bitmap_info(ctypes, wt, width, height)
        bits = ctypes.c_void_p()
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(header), DIB_RGB_COLORS,
                                        ctypes.byref(bits), None, 0)
        if not mem_dc or not bitmap:
            user32.ReleaseDC(None, screen_dc)
            raise CaptureError("Out of graphics memory")
        old = gdi32.SelectObject(mem_dc, bitmap)
        try:
            if self.method == "screen":
                origin = wt.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(origin))
                ok = gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, origin.x, origin.y, SRCCOPY | CAPTUREBLT)
            else:
                ok = user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            gdi32.GdiFlush()
            if not ok:
                raise CaptureError("Window could not be captured")
            pixels = bytearray(ctypes.string_at(bits, width * height * 4))
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
        if is_mostly_black(pixels):
            hint = ("Try Settings › Previews › Capture method › Screen copy."
                    if self.method == "printwindow" else "Is the window covered or off-screen?")
            raise CaptureError(f"The captured picture is black. {hint}")
        return bgra_to_qimage(pixels, width, height, max_width)


def _bitmap_info(ctypes, wt, width: int, height: int):
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    info = BITMAPINFO()
    h = info.bmiHeader
    h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    h.biWidth, h.biHeight = width, -height  # negative height: top-down rows, like QImage
    h.biPlanes, h.biBitCount, h.biCompression = 1, 32, BI_RGB
    return info
