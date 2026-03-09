"""Cross-platform window management.

Supports:
- Windows (via ctypes/win32 API)
- WSL (via PowerShell calling into Windows .NET / Win32 APIs)

Provides listing, focusing, resizing, moving, and state control
(minimize, maximize, restore, close) for desktop windows.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Literal

from pov.screenshot import is_wsl, _powershell_path


# ---------------------------------------------------------------------------
# PowerShell script for WSL window management
# ---------------------------------------------------------------------------

_PS_WINDOW_SCRIPT = r"""
param()

Add-Type @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public class PovWindow {

    // --- delegates & callbacks ---
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    // --- user32.dll imports ---
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll", EntryPoint = "GetWindowThreadProcessId")]
    private static extern uint _GetWindowThreadProcessId_Ref(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll", EntryPoint = "GetWindowThreadProcessId")]
    private static extern uint _GetWindowThreadProcessId_Ptr(IntPtr hWnd, IntPtr lpdwProcessId);

    /// <summary>Helper to get the owning PID of a window.</summary>
    public static uint GetOwnerPid(IntPtr hWnd) {
        uint pid = 0;
        _GetWindowThreadProcessId_Ref(hWnd, out pid);
        return pid;
    }

    /// <summary>Get thread ID only (pass IntPtr.Zero for pid).</summary>
    public static uint GetWindowThreadId(IntPtr hWnd) {
        return _GetWindowThreadProcessId_Ptr(hWnd, IntPtr.Zero);
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsZoomed(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
        int X, int Y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll")]
    public static extern IntPtr SetFocus(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();

    // ShowWindow constants
    public const int SW_HIDE      = 0;
    public const int SW_NORMAL    = 1;
    public const int SW_MINIMIZE  = 6;
    public const int SW_RESTORE   = 9;
    public const int SW_MAXIMIZE  = 3;
    public const int SW_SHOW      = 5;

    // SetWindowPos flags
    public const uint SWP_NOMOVE     = 0x0002;
    public const uint SWP_NOSIZE     = 0x0001;
    public const uint SWP_SHOWWINDOW = 0x0040;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    /// <summary>Forcefully bring a window to front, working around focus-stealing prevention.</summary>
    public static void ForceForeground(IntPtr hWnd) {
        IntPtr foreWnd = GetForegroundWindow();
        uint foreThread = GetWindowThreadId(foreWnd);
        uint curThread = GetCurrentThreadId();

        if (foreThread != curThread) {
            AttachThreadInput(curThread, foreThread, true);
            SetForegroundWindow(hWnd);
            BringWindowToTop(hWnd);
            AttachThreadInput(curThread, foreThread, false);
        } else {
            SetForegroundWindow(hWnd);
            BringWindowToTop(hWnd);
        }
    }
}
"@

$input_json = '__INPUT_JSON__' | ConvertFrom-Json
$action = $input_json.action

switch ($action) {
    "list_windows" {
        $results = @()
        $procs = @{}
        Get-Process | ForEach-Object { $procs[$_.Id] = $_.ProcessName }

        $callback = {
            param($hWnd, $lParam)
            if ([PovWindow]::IsWindowVisible($hWnd)) {
                $len = [PovWindow]::GetWindowTextLength($hWnd)
                if ($len -gt 0) {
                    $sb = New-Object System.Text.StringBuilder($len + 1)
                    [PovWindow]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
                    $title = $sb.ToString()

                    $wpid = [PovWindow]::GetOwnerPid($hWnd)

                    $rect = New-Object PovWindow+RECT
                    [PovWindow]::GetWindowRect($hWnd, [ref]$rect) | Out-Null

                    $classSb = New-Object System.Text.StringBuilder(256)
                    [PovWindow]::GetClassName($hWnd, $classSb, $classSb.Capacity) | Out-Null

                    $procName = ""
                    if ($procs.ContainsKey([int]$wpid)) {
                        $procName = $procs[[int]$wpid]
                    }

                    $isMinimized = [PovWindow]::IsIconic($hWnd)
                    $isMaximized = [PovWindow]::IsZoomed($hWnd)

                    $state = "normal"
                    if ($isMinimized) { $state = "minimized" }
                    elseif ($isMaximized) { $state = "maximized" }

                    $script:results += @{
                        hwnd         = [long]$hWnd
                        title        = $title
                        process_name = $procName
                        pid          = [int]$wpid
                        class_name   = $classSb.ToString()
                        state        = $state
                        left         = $rect.Left
                        top          = $rect.Top
                        width        = $rect.Right - $rect.Left
                        height       = $rect.Bottom - $rect.Top
                    }
                }
            }
            return $true
        }
        [PovWindow]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
        $results | ConvertTo-Json -Compress -Depth 3
    }

    "focus_window" {
        $hwnd = [IntPtr][long]$input_json.hwnd
        [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_RESTORE) | Out-Null
        Start-Sleep -Milliseconds 50
        [PovWindow]::ForceForeground($hwnd)
        Write-Output '{"ok": true}'
    }

    "set_window_state" {
        $hwnd = [IntPtr][long]$input_json.hwnd
        $state = $input_json.state

        switch ($state) {
            "minimize"  { [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_MINIMIZE) | Out-Null }
            "maximize"  { [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_MAXIMIZE) | Out-Null }
            "restore"   { [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_RESTORE)  | Out-Null }
            "hide"      { [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_HIDE)     | Out-Null }
            "show"      { [PovWindow]::ShowWindow($hwnd, [PovWindow]::SW_SHOW)     | Out-Null }
            default     { Write-Error "Unknown state: $state"; exit 1 }
        }
        Write-Output '{"ok": true}'
    }

    "move_window" {
        $hwnd = [IntPtr][long]$input_json.hwnd
        $x = [int]$input_json.x
        $y = [int]$input_json.y
        $w = [int]$input_json.width
        $h = [int]$input_json.height

        # If width/height are -1, keep current size
        if ($w -eq -1 -or $h -eq -1) {
            $rect = New-Object PovWindow+RECT
            [PovWindow]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
            if ($w -eq -1) { $w = $rect.Right - $rect.Left }
            if ($h -eq -1) { $h = $rect.Bottom - $rect.Top }
        }
        # If x/y are -1, keep current position
        if ($x -eq -1 -or $y -eq -1) {
            $rect2 = New-Object PovWindow+RECT
            [PovWindow]::GetWindowRect($hwnd, [ref]$rect2) | Out-Null
            if ($x -eq -1) { $x = $rect2.Left }
            if ($y -eq -1) { $y = $rect2.Top }
        }

        [PovWindow]::MoveWindow($hwnd, $x, $y, $w, $h, $true) | Out-Null
        Write-Output '{"ok": true}'
    }

    "resize_window" {
        $hwnd = [IntPtr][long]$input_json.hwnd
        $w = [int]$input_json.width
        $h = [int]$input_json.height

        $rect = New-Object PovWindow+RECT
        [PovWindow]::GetWindowRect($hwnd, [ref]$rect) | Out-Null

        $curW = $rect.Right - $rect.Left
        $curH = $rect.Bottom - $rect.Top
        if ($w -eq -1) { $w = $curW }
        if ($h -eq -1) { $h = $curH }

        [PovWindow]::MoveWindow($hwnd, $rect.Left, $rect.Top, $w, $h, $true) | Out-Null
        Write-Output '{"ok": true}'
    }

    "get_foreground" {
        $hwnd = [PovWindow]::GetForegroundWindow()
        $len = [PovWindow]::GetWindowTextLength($hwnd)
        $sb = New-Object System.Text.StringBuilder($len + 1)
        [PovWindow]::GetWindowText($hwnd, $sb, $sb.Capacity) | Out-Null

        $wpid = [PovWindow]::GetOwnerPid($hwnd)

        $procName = ""
        try { $procName = (Get-Process -Id $wpid).ProcessName } catch {}

        @{
            hwnd         = [long]$hwnd
            title        = $sb.ToString()
            process_name = $procName
            pid          = [int]$wpid
        } | ConvertTo-Json -Compress
    }

    "close_window" {
        $hwnd = [IntPtr][long]$input_json.hwnd
        # Send WM_CLOSE
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinMsg {
            [DllImport("user32.dll")]
            public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
            public const uint WM_CLOSE = 0x0010;
        }
"@
        [WinMsg]::SendMessage($hwnd, [WinMsg]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        Write-Output '{"ok": true}'
    }

    "list_processes" {
        $results = @()
        Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | ForEach-Object {
            $results += @{
                pid          = $_.Id
                process_name = $_.ProcessName
                title        = $_.MainWindowTitle
                responding   = $_.Responding
                memory_mb    = [math]::Round($_.WorkingSet64 / 1MB, 1)
            }
        }
        $results | ConvertTo-Json -Compress -Depth 3
    }

    default {
        Write-Error "Unknown action: $action"
        exit 1
    }
}
"""


# ---------------------------------------------------------------------------
# WSL backend
# ---------------------------------------------------------------------------


def _wsl_run_window(action: str, **kwargs: object) -> Any:
    """Run the PowerShell window helper and return parsed JSON output."""
    ps = _powershell_path()
    payload = json.dumps({"action": action, **kwargs})
    # Escape single quotes for PowerShell single-quoted string embedding
    payload_escaped = payload.replace("'", "''")
    script = _PS_WINDOW_SCRIPT.replace("'__INPUT_JSON__'", f"'{payload_escaped}'")

    result = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell window command failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        return {"ok": True}
    return json.loads(stdout)


def _run_ps_native(action: str, **kwargs: object) -> Any:
    """Run the PS window script on native Windows (via powershell.exe)."""
    payload = json.dumps({"action": action, **kwargs})
    # Escape single quotes for PowerShell single-quoted string embedding
    payload_escaped = payload.replace("'", "''")
    script = _PS_WINDOW_SCRIPT.replace("'__INPUT_JSON__'", f"'{payload_escaped}'")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell window command failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        return {"ok": True}
    return json.loads(stdout)


def _run(action: str, **kwargs: object) -> Any:
    """Route to WSL or native backend."""
    if is_wsl():
        return _wsl_run_window(action, **kwargs)
    return _run_ps_native(action, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_windows() -> list[dict]:
    """List all visible windows with titles.

    Returns a list of dicts with keys: ``hwnd``, ``title``,
    ``process_name``, ``pid``, ``class_name``, ``state``,
    ``left``, ``top``, ``width``, ``height``.
    """
    result = _run("list_windows")
    if isinstance(result, dict):
        return [result]
    return result


def list_processes() -> list[dict]:
    """List processes that have visible main windows.

    Returns a list of dicts with keys: ``pid``, ``process_name``,
    ``title``, ``responding``, ``memory_mb``.
    """
    result = _run("list_processes")
    if isinstance(result, dict):
        return [result]
    return result


def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground and give it focus.

    Parameters
    ----------
    hwnd:
        The window handle (from ``list_windows``).
    """
    return _run("focus_window", hwnd=hwnd)


def set_window_state(
    hwnd: int,
    state: Literal["minimize", "maximize", "restore", "hide", "show"],
) -> dict:
    """Change a window's display state.

    Parameters
    ----------
    hwnd:
        The window handle (from ``list_windows``).
    state:
        One of ``minimize``, ``maximize``, ``restore``, ``hide``, ``show``.
    """
    return _run("set_window_state", hwnd=hwnd, state=state)


def move_window(
    hwnd: int,
    *,
    x: int = -1,
    y: int = -1,
    width: int = -1,
    height: int = -1,
) -> dict:
    """Move and/or resize a window.

    Pass ``-1`` for any parameter to keep its current value.

    Parameters
    ----------
    hwnd:
        The window handle.
    x:
        New left edge in pixels (``-1`` = keep current).
    y:
        New top edge in pixels (``-1`` = keep current).
    width:
        New width in pixels (``-1`` = keep current).
    height:
        New height in pixels (``-1`` = keep current).
    """
    return _run("move_window", hwnd=hwnd, x=x, y=y, width=width, height=height)


def resize_window(
    hwnd: int,
    *,
    width: int = -1,
    height: int = -1,
) -> dict:
    """Resize a window without moving it.

    Pass ``-1`` for either dimension to keep its current value.

    Parameters
    ----------
    hwnd:
        The window handle.
    width:
        New width in pixels (``-1`` = keep current).
    height:
        New height in pixels (``-1`` = keep current).
    """
    return _run("resize_window", hwnd=hwnd, width=width, height=height)


def get_foreground_window() -> dict:
    """Return info about the currently focused (foreground) window.

    Returns a dict with ``hwnd``, ``title``, ``process_name``, ``pid``.
    """
    return _run("get_foreground")


def close_window(hwnd: int) -> dict:
    """Send a close (WM_CLOSE) message to a window.

    This is a graceful close -- the application may prompt to save, etc.

    Parameters
    ----------
    hwnd:
        The window handle.
    """
    return _run("close_window", hwnd=hwnd)
