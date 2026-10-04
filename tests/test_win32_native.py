"""Tests for the native Win32 backend (parsing + structure correctness).

Native calls that touch the live desktop are deliberately excluded; those are
covered by the manual functional suite (see docs/verification.md).
"""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")


def test_input_structure_size_matches_winapi():
    """SendInput requires sizeof(INPUT) == 40 on x64 / 28 on x86."""
    from pov.win32_native import INPUT

    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected


def test_key_vocabulary_covers_sendkeys_names():
    """Every friendly name the SendKeys map knows must exist natively."""
    from pov.input import _SENDKEYS_MAP
    from pov.win32_native import _KEY_NAME_VK

    missing = set(_SENDKEYS_MAP) - set(_KEY_NAME_VK)
    assert not missing, f"missing native key names: {missing}"


def test_unicode_events_handle_surrogate_pairs():
    from pov.win32_native import _unicode_inputs, KEYEVENTF_UNICODE

    events = _unicode_inputs("🎉")  # one char, two UTF-16 units
    assert len(events) == 4  # down+up per surrogate
    assert all(e.type == 1 for e in events)
    assert all(e.ki.dwFlags & KEYEVENTF_UNICODE for e in events)
    assert [e.ki.wScan for e in events[::2]] == [0xD83C, 0xDF89]


def test_invalid_hwnd_rejected():
    from pov.win32_native import native_focus_window

    with pytest.raises(ValueError):
        native_focus_window(0)


def test_unknown_state_rejected():
    from pov.win32_native import native_set_window_state
    import pov.win32_native as native

    hwnd = native.user32.GetForegroundWindow()
    with pytest.raises(ValueError, match="Unknown state"):
        native_set_window_state(int(hwnd), "bogus")
