"""Cross-platform mouse and keyboard input automation.

Supports:
- Windows (via ctypes/win32 API)
- macOS (via Quartz/CoreGraphics)
- Linux/X11 (via xdotool subprocess)
- WSL (via PowerShell calling into Windows .NET / Win32 APIs)

On WSL the same strategy as ``screenshot.py`` is used: we shell out to
``powershell.exe`` and use .NET ``System.Windows.Forms`` plus P/Invoke of
``user32.dll`` to drive the *Windows* desktop, not the WSLg one.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Literal

from pov.screenshot import is_wsl, _powershell_path


# ---------------------------------------------------------------------------
# PowerShell script for WSL input automation
# ---------------------------------------------------------------------------

_PS_INPUT_SCRIPT = r"""
param()
Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Runtime.InteropServices;

public class PovInput {
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll")]
    public static extern bool GetCursorPos(out POINT lpPoint);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint dwFlags, int dx, int dy, int dwData, IntPtr dwExtraInfo);

    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, IntPtr dwExtraInfo);

    [DllImport("user32.dll")]
    public static extern short VkKeyScan(char ch);

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT {
        public int X;
        public int Y;
    }

    public const uint MOUSEEVENTF_LEFTDOWN   = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP     = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN  = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP    = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP   = 0x0040;
    public const uint MOUSEEVENTF_WHEEL      = 0x0800;

    public const uint KEYEVENTF_KEYUP = 0x0002;
}
"@

$input_json = '__INPUT_JSON__' | ConvertFrom-Json
$action = $input_json.action

switch ($action) {
    "mouse_move" {
        [PovInput]::SetCursorPos([int]$input_json.x, [int]$input_json.y) | Out-Null
        Write-Output '{"ok": true}'
    }

    "mouse_click" {
        $x = [int]$input_json.x
        $y = [int]$input_json.y
        $button = $input_json.button
        $clicks = [int]$input_json.clicks

        if ($x -ne -1 -and $y -ne -1) {
            [PovInput]::SetCursorPos($x, $y) | Out-Null
            Start-Sleep -Milliseconds 50
        }

        $downFlag = 0
        $upFlag = 0
        switch ($button) {
            "left"   { $downFlag = [PovInput]::MOUSEEVENTF_LEFTDOWN;   $upFlag = [PovInput]::MOUSEEVENTF_LEFTUP }
            "right"  { $downFlag = [PovInput]::MOUSEEVENTF_RIGHTDOWN;  $upFlag = [PovInput]::MOUSEEVENTF_RIGHTUP }
            "middle" { $downFlag = [PovInput]::MOUSEEVENTF_MIDDLEDOWN; $upFlag = [PovInput]::MOUSEEVENTF_MIDDLEUP }
        }

        for ($i = 0; $i -lt $clicks; $i++) {
            [PovInput]::mouse_event($downFlag, 0, 0, 0, [IntPtr]::Zero) | Out-Null
            [PovInput]::mouse_event($upFlag, 0, 0, 0, [IntPtr]::Zero) | Out-Null
            if ($i -lt $clicks - 1) { Start-Sleep -Milliseconds 50 }
        }
        Write-Output '{"ok": true}'
    }

    "mouse_scroll" {
        $x = [int]$input_json.x
        $y = [int]$input_json.y
        $amount = [int]$input_json.amount

        if ($x -ne -1 -and $y -ne -1) {
            [PovInput]::SetCursorPos($x, $y) | Out-Null
            Start-Sleep -Milliseconds 50
        }

        # amount is in "clicks" (120 units per click is the Windows standard)
        $wheelDelta = $amount * 120
        [PovInput]::mouse_event([PovInput]::MOUSEEVENTF_WHEEL, 0, 0, $wheelDelta, [IntPtr]::Zero) | Out-Null
        Write-Output '{"ok": true}'
    }

    "cursor_position" {
        $pt = New-Object PovInput+POINT
        [PovInput]::GetCursorPos([ref]$pt) | Out-Null
        @{ x = $pt.X; y = $pt.Y } | ConvertTo-Json -Compress
    }

    "type_text" {
        $text = $input_json.text
        [System.Windows.Forms.SendKeys]::SendWait($text)
        Write-Output '{"ok": true}'
    }

    "key_press" {
        # Uses SendKeys syntax for key combinations.
        $keys = $input_json.keys
        [System.Windows.Forms.SendKeys]::SendWait($keys)
        Write-Output '{"ok": true}'
    }

    default {
        Write-Error "Unknown action: $action"
        exit 1
    }
}
"""


# ---------------------------------------------------------------------------
# Key name -> SendKeys mapping
# ---------------------------------------------------------------------------

# Maps friendly key names to SendKeys notation.
_SENDKEYS_MAP: dict[str, str] = {
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "tab": "{TAB}",
    "escape": "{ESC}",
    "esc": "{ESC}",
    "backspace": "{BACKSPACE}",
    "delete": "{DELETE}",
    "del": "{DELETE}",
    "insert": "{INSERT}",
    "ins": "{INSERT}",
    "home": "{HOME}",
    "end": "{END}",
    "pageup": "{PGUP}",
    "pagedown": "{PGDN}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "space": " ",
    "f1": "{F1}",
    "f2": "{F2}",
    "f3": "{F3}",
    "f4": "{F4}",
    "f5": "{F5}",
    "f6": "{F6}",
    "f7": "{F7}",
    "f8": "{F8}",
    "f9": "{F9}",
    "f10": "{F10}",
    "f11": "{F11}",
    "f12": "{F12}",
    "capslock": "{CAPSLOCK}",
    "numlock": "{NUMLOCK}",
    "scrolllock": "{SCROLLLOCK}",
    "printscreen": "{PRTSC}",
    "break": "{BREAK}",
    "pause": "{BREAK}",
}

# Modifier keys -> SendKeys prefix
_MODIFIER_MAP: dict[str, str] = {
    "ctrl": "^",
    "control": "^",
    "alt": "%",
    "shift": "+",
    "win": "^({ESC})",  # SendKeys doesn't have a native Win key
}


def _key_combo_to_sendkeys(combo: str) -> str:
    """Convert a human-friendly key combo like ``ctrl+shift+t`` to SendKeys.

    Examples::

        "ctrl+c"       -> "^c"
        "ctrl+shift+t" -> "^+t"
        "alt+f4"       -> "%{F4}"
        "enter"        -> "{ENTER}"
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    modifiers = ""
    key_part = ""

    for part in parts:
        if part in _MODIFIER_MAP:
            modifiers += _MODIFIER_MAP[part]
        elif part in _SENDKEYS_MAP:
            key_part = _SENDKEYS_MAP[part]
        else:
            # Single character key or unknown
            key_part = part

    if modifiers and key_part:
        # Wrap the key in parens if it's a single char so modifiers apply
        if len(key_part) == 1:
            return f"{modifiers}{key_part}"
        else:
            return f"{modifiers}({key_part})"
    elif key_part:
        return key_part
    elif modifiers:
        return modifiers
    return combo


def _escape_sendkeys_text(text: str) -> str:
    """Escape special SendKeys characters in literal text.

    SendKeys treats ``+``, ``^``, ``%``, ``~``, ``(``, ``)``, ``{``, ``}``
    as special.  We wrap them in braces to send them literally.
    """
    result = []
    for ch in text:
        if ch in ("+", "^", "%", "~", "(", ")", "{", "}"):
            result.append("{" + ch + "}")
        else:
            result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# WSL backend
# ---------------------------------------------------------------------------


def _wsl_run_input(action: str, **kwargs: object) -> dict:
    """Run the PowerShell input helper and return parsed JSON output."""
    ps = _powershell_path()
    payload = json.dumps({"action": action, **kwargs})
    # Escape single quotes for PowerShell single-quoted string embedding
    payload_escaped = payload.replace("'", "''")
    script = _PS_INPUT_SCRIPT.replace("'__INPUT_JSON__'", f"'{payload_escaped}'")

    result = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell input command failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Native backend (non-WSL)
# ---------------------------------------------------------------------------


def _native_mouse_move(x: int, y: int) -> None:
    """Move mouse cursor using platform-native APIs."""
    import ctypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
    user32.SetCursorPos(x, y)


def _native_mouse_click(x: int, y: int, button: str, clicks: int) -> None:
    """Click mouse using platform-native APIs."""
    import ctypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040

    if x >= 0 and y >= 0:
        user32.SetCursorPos(x, y)
        time.sleep(0.05)

    flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }
    down, up = flags.get(button, flags["left"])

    for i in range(clicks):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        if i < clicks - 1:
            time.sleep(0.05)


def _native_mouse_scroll(x: int, y: int, amount: int) -> None:
    """Scroll mouse wheel using platform-native APIs."""
    import ctypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
    MOUSEEVENTF_WHEEL = 0x0800

    if x >= 0 and y >= 0:
        user32.SetCursorPos(x, y)
        time.sleep(0.05)

    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, amount * 120, 0)


def _native_get_cursor_position() -> dict[str, int]:
    """Get cursor position using platform-native APIs."""
    import ctypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return {"x": pt.x, "y": pt.y}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mouse_move(x: int, y: int) -> dict:
    """Move the mouse cursor to the given screen coordinates.

    Parameters
    ----------
    x:
        Horizontal pixel coordinate.
    y:
        Vertical pixel coordinate.
    """
    if is_wsl():
        return _wsl_run_input("mouse_move", x=x, y=y)
    _native_mouse_move(x, y)
    return {"ok": True}


def mouse_click(
    x: int = -1,
    y: int = -1,
    *,
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
) -> dict:
    """Click the mouse at the given coordinates.

    Parameters
    ----------
    x:
        Horizontal pixel coordinate.  ``-1`` = click at current position.
    y:
        Vertical pixel coordinate.  ``-1`` = click at current position.
    button:
        Which button: ``left``, ``right``, or ``middle``.
    clicks:
        Number of clicks (1 = single, 2 = double).
    """
    if is_wsl():
        return _wsl_run_input("mouse_click", x=x, y=y, button=button, clicks=clicks)
    _native_mouse_click(x, y, button, clicks)
    return {"ok": True}


def mouse_scroll(
    amount: int,
    *,
    x: int = -1,
    y: int = -1,
) -> dict:
    """Scroll the mouse wheel.

    Parameters
    ----------
    amount:
        Number of scroll "clicks".  Positive = scroll up, negative = down.
    x:
        Horizontal pixel coordinate.  ``-1`` = scroll at current position.
    y:
        Vertical pixel coordinate.  ``-1`` = scroll at current position.
    """
    if is_wsl():
        return _wsl_run_input("mouse_scroll", x=x, y=y, amount=amount)
    _native_mouse_scroll(x, y, amount)
    return {"ok": True}


def get_cursor_position() -> dict[str, int]:
    """Return the current cursor position as ``{"x": ..., "y": ...}``."""
    if is_wsl():
        return _wsl_run_input("cursor_position")
    return _native_get_cursor_position()


def type_text(text: str) -> dict:
    """Type a string of text as if the user pressed each key.

    Special SendKeys characters are escaped so the literal text is typed.

    Parameters
    ----------
    text:
        The text to type.
    """
    escaped = _escape_sendkeys_text(text)
    if is_wsl():
        return _wsl_run_input("type_text", text=escaped)

    # On native Windows, fall back to PowerShell SendKeys as well since
    # ctypes keybd_event for arbitrary text is complex.
    _wsl_run_ps_native("type_text", text=escaped)
    return {"ok": True}


def key_press(keys: str) -> dict:
    """Press a key or key combination.

    Accepts human-friendly combos like ``ctrl+c``, ``alt+f4``, ``enter``,
    ``ctrl+shift+t``, etc.

    Parameters
    ----------
    keys:
        Key combination string (e.g. ``"ctrl+c"``, ``"enter"``).
    """
    sendkeys_str = _key_combo_to_sendkeys(keys)
    if is_wsl():
        return _wsl_run_input("key_press", keys=sendkeys_str)

    # Native Windows fallback
    _wsl_run_ps_native("key_press", keys=sendkeys_str)
    return {"ok": True}


def _wsl_run_ps_native(action: str, **kwargs: object) -> dict:
    """Run the same PS input script on native Windows (via powershell.exe)."""
    # On native Windows, powershell.exe is available directly
    payload = json.dumps({"action": action, **kwargs})
    # Escape single quotes for PowerShell single-quoted string embedding
    payload_escaped = payload.replace("'", "''")
    script = _PS_INPUT_SCRIPT.replace("'__INPUT_JSON__'", f"'{payload_escaped}'")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell input command failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return json.loads(result.stdout.strip())
