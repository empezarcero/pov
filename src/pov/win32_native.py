"""Native Win32 backend (ctypes, no subprocess).

Replaces the PowerShell-spawning paths for window management and keyboard
input on native Windows.  Each PowerShell spawn cost ~2-3 s (process start +
.NET JIT of the embedded C# helper); calling user32.dll directly brings every
operation into the sub-millisecond range and lets a long-running MCP server
service input requests interactively.

WSL keeps the PowerShell bridge (it genuinely needs it).  On other platforms
this module is never imported by the dispatchers.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import functools
import os

if os.name != "nt":  # pragma: no cover - import guard for non-Windows dev boxes
    raise ImportError("pov.win32_native is Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

# POINTER-sized on both arches; SIZE_T == ULONG_PTR on Windows.
ULONG_PTR = ctypes.c_size_t

# ---------------------------------------------------------------------------
# DPI awareness: make every coordinate physical so window rects, cursor
# positions, and mss captures all live in the same space (mixed DPI setups
# otherwise return virtualized rects that do not match the screenshot).
# ---------------------------------------------------------------------------

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


@functools.lru_cache(maxsize=1)
def init_dpi_awareness() -> None:
    """Best-effort DPI awareness; failures fall back to virtualized coords."""
    try:
        if not user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            # Old Windows (<1703) without the context API.
            user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", w.LONG),
        ("top", w.LONG),
        ("right", w.LONG),
        ("bottom", w.LONG),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", w.LONG),
        ("dy", w.LONG),
        ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", w.WORD),
        ("wScan", w.WORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUT_UNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

SW_HIDE = 0
SW_NORMAL = 1
SW_MAXIMIZE = 3
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9

WM_CLOSE = 0x0010

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.GetForegroundWindow.restype = w.HWND
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT
user32.VkKeyScanW.argtypes = [w.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short


def _check_handle(hwnd: int) -> w.HWND:
    if not hwnd:
        raise ValueError("hwnd must be a non-zero window handle")
    handle = w.HWND(hwnd)
    if not user32.IsWindow(handle):
        raise ValueError(f"hwnd {hwnd} is not a valid window")
    return handle


# ---------------------------------------------------------------------------
# Window management (replaces the PowerShell window script on native Windows)
# ---------------------------------------------------------------------------


def _window_text(hwnd: w.HWND) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class_name(hwnd: w.HWND) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_pid(hwnd: w.HWND) -> int:
    pid = w.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _window_rect(hwnd: w.HWND) -> tuple[int, int, int, int]:
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value)
            # Match Get-Process ProcessName: no file extension.
            if name.lower().endswith(".exe"):
                name = name[:-4]
            return name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _process_memory_mb(pid: int) -> float:
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", w.DWORD),
            ("PageFaultCount", w.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        ):
            return round(counters.WorkingSetSize / (1024 * 1024), 1)
        return 0.0
    finally:
        kernel32.CloseHandle(handle)


def _window_state(hwnd: w.HWND) -> str:
    if user32.IsIconic(hwnd):
        return "minimized"
    if user32.IsZoomed(hwnd):
        return "maximized"
    return "normal"


def native_list_windows() -> list[dict]:
    """EnumWindows: visible windows with a non-empty title, z-order top-down."""
    init_dpi_awareness()
    results: list[dict] = []

    @ENUMWINDOWSPROC
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(hwnd)
        if not title:
            return True
        pid = _window_pid(hwnd)
        left, top, width, height = _window_rect(hwnd)
        results.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "process_name": _process_name(pid),
                "pid": pid,
                "class_name": _class_name(hwnd),
                "state": _window_state(hwnd),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        )
        return True

    user32.EnumWindows(_callback, 0)
    return results


def native_list_processes() -> list[dict]:
    """GUI processes: one row per pid that owns a visible window.

    The first (topmost z-order) window of a process stands in for its main
    window; ``responding`` comes from IsHungAppWindow.
    """
    seen: dict[int, dict] = {}
    for winfo in native_list_windows():
        pid = winfo["pid"]
        if pid in seen:
            continue
        hwnd = w.HWND(winfo["hwnd"])
        seen[pid] = {
            "pid": pid,
            "process_name": winfo["process_name"],
            "title": winfo["title"],
            "responding": not user32.IsHungAppWindow(hwnd),
            "memory_mb": _process_memory_mb(pid),
        }
    return list(seen.values())


def native_focus_window(hwnd: int) -> dict:
    """Restore + foreground, working around Windows focus-stealing prevention."""
    init_dpi_awareness()
    handle = _check_handle(hwnd)
    user32.ShowWindow(handle, SW_RESTORE)
    import time

    time.sleep(0.05)
    foreground = user32.GetForegroundWindow()
    fore_thread = user32.GetWindowThreadProcessId(foreground, None)
    current_thread = kernel32.GetCurrentThreadId()
    if fore_thread != current_thread:
        user32.AttachThreadInput(current_thread, fore_thread, True)
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
        user32.AttachThreadInput(current_thread, fore_thread, False)
    else:
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
    return {"ok": True}


def native_set_window_state(hwnd: int, state: str) -> dict:
    handle = _check_handle(hwnd)
    commands = {
        "minimize": SW_MINIMIZE,
        "maximize": SW_MAXIMIZE,
        "restore": SW_RESTORE,
        "hide": SW_HIDE,
        "show": SW_SHOW,
    }
    if state not in commands:
        raise ValueError(
            f"Unknown state: {state!r} (expected one of {sorted(commands)})"
        )
    user32.ShowWindow(handle, commands[state])
    return {"ok": True}


def native_move_window(
    hwnd: int, x: int = -1, y: int = -1, width: int = -1, height: int = -1
) -> dict:
    handle = _check_handle(hwnd)
    left, top, cur_w, cur_h = _window_rect(handle)
    if width == -1:
        width = cur_w
    if height == -1:
        height = cur_h
    if x == -1:
        x = left
    if y == -1:
        y = top
    user32.MoveWindow(handle, x, y, width, height, True)
    return {"ok": True}


def native_resize_window(hwnd: int, width: int = -1, height: int = -1) -> dict:
    handle = _check_handle(hwnd)
    left, top, cur_w, cur_h = _window_rect(handle)
    if width == -1:
        width = cur_w
    if height == -1:
        height = cur_h
    user32.MoveWindow(handle, left, top, width, height, True)
    return {"ok": True}


def native_get_foreground_window() -> dict:
    handle = user32.GetForegroundWindow()
    pid = _window_pid(handle)
    return {
        "hwnd": int(handle),
        "title": _window_text(handle),
        "process_name": _process_name(pid),
        "pid": pid,
    }


def native_close_window(hwnd: int) -> dict:
    """Post WM_CLOSE (non-blocking, so a hung target cannot stall the server)."""
    handle = _check_handle(hwnd)
    user32.PostMessageW(handle, WM_CLOSE, 0, 0)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Keyboard input via SendInput (replaces the SendKeys PowerShell bridge)
# ---------------------------------------------------------------------------

# Friendly key names -> virtual-key codes (same vocabulary as the SendKeys map
# in input.py, plus `win` which SendKeys cannot express).
_KEY_NAME_VK: dict[str, int] = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "space": 0x20,
    "capslock": 0x14,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "printscreen": 0x2C,
    "break": 0x13,
    "pause": 0x13,
    **{f"f{i}": 0x70 + (i - 1) for i in range(1, 13)},
}

_MODIFIER_VK: dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,  # VK_LWIN
}

_SHIFT_STATE_SHIFT = 0x01
_SHIFT_STATE_CTRL = 0x02
_SHIFT_STATE_ALT = 0x04


def _send_inputs(inputs: list[INPUT]) -> None:
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())


def _key_input(vk: int, *, up: bool) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return event


def _vk_for_char(ch: str) -> tuple[int, int] | None:
    """VkKeyScanW: returns (vk, shift_state) or None when unmappable."""
    result = user32.VkKeyScanW(ch)
    if result == -1:
        return None
    return result & 0xFF, (result >> 8) & 0xFF


def _unicode_inputs(ch: str) -> list[INPUT]:
    """KEYEVENTF_UNICODE events for one character (surrogate pairs included)."""
    events: list[INPUT] = []
    data = ch.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code = int.from_bytes(data[i : i + 2], "little")
        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        events.extend((down, up))
    return events


def native_type_text(text: str) -> dict:
    """Type arbitrary text (full Unicode) as keyboard input."""
    if not text:
        return {"ok": True}
    events: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            events.append(_key_input(0x0D, up=False))
            events.append(_key_input(0x0D, up=True))
            continue
        events.extend(_unicode_inputs(ch))
    _send_inputs(events)
    return {"ok": True}


def native_key_press(combo: str) -> dict:
    """Press a friendly key combination like ``ctrl+shift+t`` or ``enter``."""
    parts = [part.strip().lower() for part in combo.split("+") if part.strip()]
    if not parts:
        raise ValueError("empty key combination")

    modifiers: list[int] = []
    key_vk: int | None = None
    fallback_char: str | None = None

    for part in parts:
        if part in _MODIFIER_VK:
            vk = _MODIFIER_VK[part]
            if vk not in modifiers:
                modifiers.append(vk)
        elif part in _KEY_NAME_VK:
            key_vk = _KEY_NAME_VK[part]
        elif len(part) == 1:
            scanned = _vk_for_char(part)
            if scanned is None:
                fallback_char = part
            else:
                key_vk, shift_state = scanned
                if shift_state & _SHIFT_STATE_SHIFT and 0x10 not in modifiers:
                    modifiers.append(0x10)
                if shift_state & _SHIFT_STATE_CTRL and 0x11 not in modifiers:
                    modifiers.append(0x11)
                if shift_state & _SHIFT_STATE_ALT and 0x12 not in modifiers:
                    modifiers.append(0x12)
        else:
            raise ValueError(f"unknown key name: {part!r}")

    events: list[INPUT] = []
    events.extend(_key_input(vk, up=False) for vk in modifiers)
    if key_vk is not None:
        events.append(_key_input(key_vk, up=False))
        events.append(_key_input(key_vk, up=True))
    events.extend(_key_input(vk, up=True) for vk in reversed(modifiers))
    if events:
        _send_inputs(events)
    # A character with no keyboard mapping (e.g. CJK on a US layout) is
    # typed via KEYEVENTF_UNICODE after the modifiers are released.
    if fallback_char is not None:
        _send_inputs(_unicode_inputs(fallback_char))
    return {"ok": True}
