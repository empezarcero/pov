# pov - Point of View

Screenshot capture, mouse/keyboard control, and window management — as a CLI and MCP server for LLM vision agents.

Lets LLMs (Claude Desktop, Cursor, OpenCode) **see** and **interact with** the Windows desktop.
Works on **Windows**, **macOS**, **Linux** (X11/Wayland), and **WSL**.

## Install

```bash
uv sync
```

Or install globally:

```bash
uv tool install .
```

## CLI Usage

```bash
# Screenshots
pov capture                     # Capture screenshot, print base64
pov capture --output shot.png   # Save to file
pov capture --monitor 1         # Specific monitor
pov monitors                    # List available monitors

# Mouse
pov click                       # Left-click at current position
pov click 500 300               # Left-click at (500, 300)
pov click 500 300 --button right --clicks 2
pov move-mouse 800 600          # Move cursor to (800, 600)
pov scroll -3                   # Scroll down 3 clicks
pov scroll 5 --x 400 --y 200   # Scroll up at position
pov cursor                      # Print current cursor position

# Keyboard
pov type "Hello, world!"        # Type text literally
pov key enter                   # Press a key
pov key ctrl+c                  # Key combo
pov key alt+f4                  # Another combo

# Windows
pov windows                     # List visible windows
pov processes                   # List windowed processes
pov foreground                  # Get the focused window
pov focus <hwnd>                # Focus a window by handle
pov window-state <hwnd> minimize
pov move-window <hwnd> --x 100 --y 100
pov resize-window <hwnd> --width 1024 --height 768
pov close-window <hwnd>         # Gracefully close a window

# MCP server
pov serve                       # stdio transport (default)
pov serve --transport sse --port 8000
```

## MCP Server

The MCP server exposes 16 tools:

### Screenshots
| Tool | Description |
|------|-------------|
| `screenshot` | Capture a screenshot and return it as an image |
| `list_monitors` | List available monitors and their geometry |

### Mouse
| Tool | Description |
|------|-------------|
| `mouse_move` | Move the cursor to screen coordinates |
| `mouse_click` | Click at coordinates (or current position) |
| `mouse_scroll` | Scroll the mouse wheel |
| `get_cursor_position` | Get the current cursor position |

### Keyboard
| Tool | Description |
|------|-------------|
| `keyboard_type` | Type a string of text literally |
| `keyboard_key` | Press a key or combo (e.g. `ctrl+c`, `alt+f4`) |

### Window Management
| Tool | Description |
|------|-------------|
| `list_windows` | List all visible windows with handles, titles, positions |
| `focus_window` | Bring a window to the foreground |
| `set_window_state` | Minimize, maximize, restore, hide, or show a window |
| `move_window` | Move and/or resize a window |
| `resize_window` | Resize a window without moving it |
| `get_foreground_window` | Get info about the currently focused window |
| `close_window` | Gracefully close a window (WM_CLOSE) |
| `list_processes` | List running processes with visible windows |

### Claude Desktop / Cursor / OpenCode

Add to your MCP config:

```json
{
  "mcpServers": {
    "pov": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/pov", "pov", "serve"]
    }
  }
}
```

### SSE Transport

```json
{
  "mcpServers": {
    "pov": {
      "url": "http://127.0.0.1:8000/sse"
    }
  }
}
```

## Platform Support

| Platform | Backend | Notes |
|----------|---------|-------|
| Windows  | `mss` + `ctypes` | Screenshots via mss, input/windows via Win32 |
| macOS    | `mss`   | Screenshot capture only |
| Linux    | `mss`   | Screenshot capture only (X11 and Wayland) |
| WSL      | PowerShell + .NET/Win32 | Full support — captures the **Windows desktop**, controls mouse/keyboard, manages windows |

## Development

```bash
# Install in development mode
uv sync

# Run the CLI
uv run pov --help

# Run the MCP server
uv run pov serve
```
