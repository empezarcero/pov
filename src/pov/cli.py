"""Point of View - CLI interface.

Usage::

    pov capture [--monitor N] [--output PATH] [--max-width N]
    pov monitors
    pov click [--x X] [--y Y] [--button BUTTON] [--clicks N]
    pov move-mouse --x X --y Y
    pov scroll --amount N [--x X] [--y Y]
    pov type --text TEXT
    pov key --keys KEYS
    pov cursor
    pov windows
    pov processes
    pov focus --hwnd HWND
    pov window-state --hwnd HWND --state STATE
    pov serve
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import cyclopts

from pov import __version__

app = cyclopts.App(
    name="pov",
    help=(
        "Point of View -- capture screenshots, control mouse/keyboard, "
        "and manage windows.  Also runs as an MCP server for LLM clients."
    ),
    version=__version__,
)


# ── Screenshot commands ────────────────────────────────────────────────────


@app.command
def capture(
    output: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="Save screenshot to this path instead of printing base64.",
        ),
    ] = None,
    monitor: Annotated[
        int,
        cyclopts.Parameter(help="Monitor index (0 = all monitors combined)."),
    ] = 0,
    max_width: Annotated[
        int,
        cyclopts.Parameter(help="Max width in pixels (0 = no resize)."),
    ] = 1920,
) -> None:
    """Capture a screenshot.

    If --output is given, saves the PNG to that path.
    Otherwise prints base64-encoded PNG to stdout (useful for piping to LLMs).
    """
    from pov.screenshot import capture_screenshot_b64, save_screenshot

    if output is not None:
        path = save_screenshot(output, monitor, max_width=max_width)
        print(f"Screenshot saved to {path}")
    else:
        b64 = capture_screenshot_b64(monitor, max_width=max_width)
        print(b64)


@app.command
def monitors() -> None:
    """List available monitors and their geometry."""
    from pov.screenshot import list_monitors

    mons = list_monitors()
    if not mons:
        print("No monitors detected.")
        return

    print(f"{'Index':<7} {'Left':<8} {'Top':<8} {'Width':<8} {'Height':<8}")
    print("-" * 39)
    for m in mons:
        label = "(all)" if m["index"] == 0 else ""
        print(
            f"{m['index']:<7} {m['left']:<8} {m['top']:<8} "
            f"{m['width']:<8} {m['height']:<8} {label}"
        )


# ── Mouse commands ─────────────────────────────────────────────────────────


@app.command
def click(
    x: Annotated[
        int,
        cyclopts.Parameter(help="X coordinate (-1 = current position)."),
    ] = -1,
    y: Annotated[
        int,
        cyclopts.Parameter(help="Y coordinate (-1 = current position)."),
    ] = -1,
    button: Annotated[
        str,
        cyclopts.Parameter(help="Mouse button: left, right, or middle."),
    ] = "left",
    clicks: Annotated[
        int,
        cyclopts.Parameter(help="Number of clicks (1 = single, 2 = double)."),
    ] = 1,
) -> None:
    """Click the mouse at a screen position."""
    from pov.input import mouse_click

    result = mouse_click(x, y, button=button, clicks=clicks)
    if x == -1 and y == -1:
        print(f"Clicked {button} {clicks}x at current cursor position")
    else:
        print(f"Clicked {button} {clicks}x at ({x}, {y})")


@app.command(name="move-mouse")
def move_mouse(
    x: Annotated[int, cyclopts.Parameter(help="X coordinate.")],
    y: Annotated[int, cyclopts.Parameter(help="Y coordinate.")],
) -> None:
    """Move the mouse cursor to a screen position."""
    from pov.input import mouse_move

    mouse_move(x, y)
    print(f"Moved cursor to ({x}, {y})")


@app.command
def scroll(
    amount: Annotated[
        int,
        cyclopts.Parameter(help="Scroll clicks (positive=up, negative=down)."),
    ],
    x: Annotated[
        int,
        cyclopts.Parameter(help="X coordinate (-1 = current position)."),
    ] = -1,
    y: Annotated[
        int,
        cyclopts.Parameter(help="Y coordinate (-1 = current position)."),
    ] = -1,
) -> None:
    """Scroll the mouse wheel."""
    from pov.input import mouse_scroll

    mouse_scroll(amount, x=x, y=y)
    direction = "up" if amount > 0 else "down"
    print(f"Scrolled {direction} {abs(amount)} clicks")


@app.command
def cursor() -> None:
    """Show the current mouse cursor position."""
    from pov.input import get_cursor_position

    pos = get_cursor_position()
    print(f"Cursor at ({pos['x']}, {pos['y']})")


# ── Keyboard commands ──────────────────────────────────────────────────────


@app.command(name="type")
def type_text_cmd(
    text: Annotated[str, cyclopts.Parameter(help="Text to type.")],
) -> None:
    """Type a string of text as keyboard input."""
    from pov.input import type_text

    type_text(text)
    print(f"Typed {len(text)} characters")


@app.command
def key(
    keys: Annotated[
        str,
        cyclopts.Parameter(help="Key combo (e.g. 'ctrl+c', 'enter', 'alt+tab')."),
    ],
) -> None:
    """Press a key or key combination."""
    from pov.input import key_press

    key_press(keys)
    print(f"Pressed {keys}")


# ── Window commands ────────────────────────────────────────────────────────


@app.command
def windows() -> None:
    """List all visible windows on the desktop."""
    from pov.window import list_windows

    wins = list_windows()
    if not wins:
        print("No windows found.")
        return

    print(f"{'HWND':<16} {'PID':<8} {'State':<12} {'Process':<20} {'Title'}")
    print("-" * 100)
    for w in wins:
        print(
            f"{w['hwnd']:<16} {w['pid']:<8} {w['state']:<12} "
            f"{w.get('process_name', ''):<20} {w['title'][:60]}"
        )


@app.command
def processes() -> None:
    """List running processes with visible windows."""
    from pov.window import list_processes

    procs = list_processes()
    if not procs:
        print("No GUI processes found.")
        return

    print(f"{'PID':<8} {'Memory':<10} {'Process':<20} {'Title'}")
    print("-" * 80)
    for p in procs:
        mem = f"{p['memory_mb']:.1f} MB"
        print(f"{p['pid']:<8} {mem:<10} {p['process_name']:<20} {p['title'][:40]}")


@app.command
def focus(
    hwnd: Annotated[
        int, cyclopts.Parameter(help="Window handle (from 'pov windows').")
    ],
) -> None:
    """Focus a window (bring to foreground)."""
    from pov.window import focus_window

    focus_window(hwnd)
    print(f"Focused window {hwnd}")


@app.command(name="window-state")
def window_state_cmd(
    hwnd: Annotated[int, cyclopts.Parameter(help="Window handle.")],
    state: Annotated[
        str,
        cyclopts.Parameter(
            help="State: minimize, maximize, restore, hide, show.",
        ),
    ],
) -> None:
    """Change a window's state (minimize, maximize, etc.)."""
    from pov.window import set_window_state

    set_window_state(hwnd, state)  # type: ignore[arg-type]
    print(f"Set window {hwnd} to {state}")


@app.command(name="move-window")
def move_window_cmd(
    hwnd: Annotated[int, cyclopts.Parameter(help="Window handle.")],
    x: Annotated[int, cyclopts.Parameter(help="New X position (-1 = keep).")] = -1,
    y: Annotated[int, cyclopts.Parameter(help="New Y position (-1 = keep).")] = -1,
    width: Annotated[int, cyclopts.Parameter(help="New width (-1 = keep).")] = -1,
    height: Annotated[int, cyclopts.Parameter(help="New height (-1 = keep).")] = -1,
) -> None:
    """Move and/or resize a window."""
    from pov.window import move_window

    move_window(hwnd, x=x, y=y, width=width, height=height)
    print(f"Moved/resized window {hwnd}")


@app.command(name="resize-window")
def resize_window_cmd(
    hwnd: Annotated[int, cyclopts.Parameter(help="Window handle.")],
    width: Annotated[int, cyclopts.Parameter(help="New width (-1 = keep).")] = -1,
    height: Annotated[int, cyclopts.Parameter(help="New height (-1 = keep).")] = -1,
) -> None:
    """Resize a window without moving it."""
    from pov.window import resize_window

    resize_window(hwnd, width=width, height=height)
    print(f"Resized window {hwnd}")


@app.command(name="close-window")
def close_window_cmd(
    hwnd: Annotated[int, cyclopts.Parameter(help="Window handle.")],
) -> None:
    """Close a window (graceful WM_CLOSE)."""
    from pov.window import close_window

    close_window(hwnd)
    print(f"Sent close to window {hwnd}")


@app.command
def foreground() -> None:
    """Show info about the currently focused window."""
    from pov.window import get_foreground_window

    w = get_foreground_window()
    print(f"HWND:    {w['hwnd']}")
    print(f"Title:   {w['title']}")
    print(f"Process: {w['process_name']}")
    print(f"PID:     {w['pid']}")


# ── Server command ─────────────────────────────────────────────────────────


@app.command
def serve(
    transport: Annotated[
        str,
        cyclopts.Parameter(help="MCP transport: stdio or sse."),
    ] = "stdio",
    host: Annotated[
        str,
        cyclopts.Parameter(help="Host to bind to (sse transport only)."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        cyclopts.Parameter(help="Port to bind to (sse transport only)."),
    ] = 8000,
) -> None:
    """Start the MCP server.

    Runs the Point of View MCP server so that LLM clients (Claude Desktop,
    Cursor, etc.) can request screenshots, control input, and manage windows.
    """
    from pov.server import mcp

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        print(f"Unknown transport: {transport!r}. Use 'stdio' or 'sse'.")
        raise SystemExit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
