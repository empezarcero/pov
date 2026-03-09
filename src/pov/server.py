"""Point of View - MCP server.

Exposes screenshot capture, mouse/keyboard input, and window management
as MCP tools so that LLM clients (Claude Desktop, Cursor, OpenCode, etc.)
can see and interact with the user's desktop.

Usage::

    # stdio (default, for most MCP clients)
    pov serve

    # SSE (for web-based clients)
    pov serve --transport sse --port 8000
"""

from __future__ import annotations

from typing import Literal

from mcp import types

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

mcp = FastMCP(
    name="pov",
    instructions=(
        "Point of View (pov) gives you eyes and hands on the user's desktop. "
        "Use `screenshot` to see what is on screen, `list_monitors` for "
        "display info. Use `mouse_click`, `mouse_move`, `mouse_scroll` to "
        "control the mouse. Use `keyboard_type` and `keyboard_key` for "
        "keyboard input. Use `list_windows`, `focus_window`, "
        "`set_window_state`, `move_window`, `resize_window` to manage "
        "windows. Use `list_processes` for running GUI apps."
    ),
)


# ── Screenshot tools ───────────────────────────────────────────────────────


@mcp.tool
def screenshot(
    monitor: int = 0,
    max_width: int = 1920,
) -> ToolResult:
    """Capture a screenshot of the user's screen.

    Returns the image so you can see what the user sees.

    Parameters
    ----------
    monitor:
        Which monitor to capture.  0 = all monitors combined into one
        image, 1 = primary monitor, 2 = second monitor, etc.
        Use ``list_monitors`` first if you're unsure.
    max_width:
        Maximum width in pixels.  The image is down-scaled (preserving
        aspect ratio) if it exceeds this.  Use 0 to disable.
    """
    from pov.screenshot import capture_screenshot_b64

    b64 = capture_screenshot_b64(monitor, max_width=max_width)
    return ToolResult(
        content=[
            types.ImageContent(type="image", data=b64, mimeType="image/png"),
        ]
    )


@mcp.tool
def list_monitors() -> list[dict[str, int]]:
    """List available monitors and their geometry.

    Returns a list of monitors with their index, position (left, top),
    and size (width, height).  Index 0 is the virtual bounding box that
    spans all monitors.
    """
    from pov.screenshot import list_monitors as _list_monitors

    return _list_monitors()


# ── Mouse tools ────────────────────────────────────────────────────────────


@mcp.tool
def mouse_move(x: int, y: int) -> dict:
    """Move the mouse cursor to the given screen coordinates.

    Parameters
    ----------
    x:
        Horizontal pixel coordinate.
    y:
        Vertical pixel coordinate.
    """
    from pov.input import mouse_move as _mouse_move

    return _mouse_move(x, y)


@mcp.tool
def mouse_click(
    x: int = -1,
    y: int = -1,
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
) -> dict:
    """Click the mouse at the given coordinates.

    Parameters
    ----------
    x:
        Horizontal pixel coordinate.  -1 = click at current cursor position.
    y:
        Vertical pixel coordinate.  -1 = click at current cursor position.
    button:
        Which button: "left", "right", or "middle".
    clicks:
        Number of clicks (1 = single, 2 = double).
    """
    from pov.input import mouse_click as _mouse_click

    return _mouse_click(x, y, button=button, clicks=clicks)


@mcp.tool
def mouse_scroll(
    amount: int,
    x: int = -1,
    y: int = -1,
) -> dict:
    """Scroll the mouse wheel.

    Parameters
    ----------
    amount:
        Number of scroll "clicks".  Positive = scroll up, negative = scroll
        down.
    x:
        Horizontal pixel coordinate.  -1 = scroll at current cursor position.
    y:
        Vertical pixel coordinate.  -1 = scroll at current cursor position.
    """
    from pov.input import mouse_scroll as _mouse_scroll

    return _mouse_scroll(amount, x=x, y=y)


@mcp.tool
def get_cursor_position() -> dict[str, int]:
    """Get the current mouse cursor position.

    Returns ``{"x": <int>, "y": <int>}``.
    """
    from pov.input import get_cursor_position as _get_cursor_position

    return _get_cursor_position()


# ── Keyboard tools ─────────────────────────────────────────────────────────


@mcp.tool
def keyboard_type(text: str) -> dict:
    """Type a string of text as keyboard input.

    Types the text literally -- special characters are escaped so they
    are sent as-is.

    Parameters
    ----------
    text:
        The text to type.
    """
    from pov.input import type_text

    return type_text(text)


@mcp.tool
def keyboard_key(keys: str) -> dict:
    """Press a key or key combination.

    Accepts human-friendly combos.  Examples:
    - Single keys: "enter", "tab", "escape", "f5", "space"
    - Combos: "ctrl+c", "ctrl+shift+t", "alt+f4", "ctrl+a"

    Parameters
    ----------
    keys:
        Key combination string (e.g. "ctrl+c", "enter", "alt+tab").
    """
    from pov.input import key_press

    return key_press(keys)


# ── Window management tools ────────────────────────────────────────────────


@mcp.tool
def list_windows() -> list[dict]:
    """List all visible windows on the desktop.

    Returns a list of window objects with: ``hwnd`` (window handle),
    ``title``, ``process_name``, ``pid``, ``class_name``, ``state``
    (normal/minimized/maximized), ``left``, ``top``, ``width``, ``height``.

    Use ``hwnd`` to identify a window for other window tools.
    """
    from pov.window import list_windows as _list_windows

    return _list_windows()


@mcp.tool
def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground and give it focus.

    Parameters
    ----------
    hwnd:
        The window handle (from ``list_windows``).
    """
    from pov.window import focus_window as _focus_window

    return _focus_window(hwnd)


@mcp.tool
def set_window_state(
    hwnd: int,
    state: Literal["minimize", "maximize", "restore", "hide", "show"],
) -> dict:
    """Change a window's display state (minimize, maximize, restore, etc.).

    Parameters
    ----------
    hwnd:
        The window handle (from ``list_windows``).
    state:
        One of "minimize", "maximize", "restore", "hide", "show".
    """
    from pov.window import set_window_state as _set_window_state

    return _set_window_state(hwnd, state)


@mcp.tool
def move_window(
    hwnd: int,
    x: int = -1,
    y: int = -1,
    width: int = -1,
    height: int = -1,
) -> dict:
    """Move and/or resize a window.

    Pass -1 for any parameter to keep its current value.

    Parameters
    ----------
    hwnd:
        The window handle.
    x:
        New left edge in pixels (-1 = keep current).
    y:
        New top edge in pixels (-1 = keep current).
    width:
        New width in pixels (-1 = keep current).
    height:
        New height in pixels (-1 = keep current).
    """
    from pov.window import move_window as _move_window

    return _move_window(hwnd, x=x, y=y, width=width, height=height)


@mcp.tool
def resize_window(
    hwnd: int,
    width: int = -1,
    height: int = -1,
) -> dict:
    """Resize a window without moving it.

    Pass -1 for either dimension to keep its current value.

    Parameters
    ----------
    hwnd:
        The window handle.
    width:
        New width in pixels (-1 = keep current).
    height:
        New height in pixels (-1 = keep current).
    """
    from pov.window import resize_window as _resize_window

    return _resize_window(hwnd, width=width, height=height)


@mcp.tool
def get_foreground_window() -> dict:
    """Get information about the currently focused window.

    Returns ``hwnd``, ``title``, ``process_name``, ``pid``.
    """
    from pov.window import get_foreground_window as _get_foreground_window

    return _get_foreground_window()


@mcp.tool
def close_window(hwnd: int) -> dict:
    """Gracefully close a window (sends WM_CLOSE).

    The application may prompt the user to save before closing.

    Parameters
    ----------
    hwnd:
        The window handle.
    """
    from pov.window import close_window as _close_window

    return _close_window(hwnd)


@mcp.tool
def list_processes() -> list[dict]:
    """List running processes that have visible windows.

    Returns a list with ``pid``, ``process_name``, ``title``,
    ``responding`` (bool), and ``memory_mb``.
    """
    from pov.window import list_processes as _list_processes

    return _list_processes()


if __name__ == "__main__":
    mcp.run()
