"""Cross-platform screenshot capture.

Supports:
- Windows (via mss)
- macOS (via mss)
- Linux/X11/Wayland (via mss)
- WSL (via PowerShell calling into Windows .NET APIs)

On WSL, ``mss`` would only capture the WSLg virtual display (Linux GUI apps),
not the actual Windows desktop.  We detect WSL and shell out to
``powershell.exe`` with .NET ``System.Windows.Forms.Screen`` /
``System.Drawing`` to capture the real Windows screen.
"""

from __future__ import annotations

import base64
import functools
import io
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


# ---------------------------------------------------------------------------
# WSL detection
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def is_wsl() -> bool:
    """Return ``True`` if we are running inside WSL."""
    if sys.platform != "linux":
        return False
    try:
        version = Path("/proc/version").read_text()
        return "microsoft" in version.lower() or "wsl" in version.lower()
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def _powershell_path() -> str:
    """Return the path to ``powershell.exe`` reachable from WSL."""
    candidates = [
        "powershell.exe",  # if it's on PATH
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]
    for candidate in candidates:
        try:
            subprocess.run(
                [candidate, "-NoProfile", "-Command", "echo ok"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            return candidate
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
    raise RuntimeError(
        "Cannot find powershell.exe. "
        "Make sure Windows PowerShell is accessible from WSL."
    )


# ---------------------------------------------------------------------------
# WSL capture via PowerShell
# ---------------------------------------------------------------------------

# PowerShell script that captures the Windows desktop and writes base64 PNG
# to stdout.  Accepts a JSON object on stdin with:
#   monitor  (int)  - 0 = virtual screen (all monitors), 1+ = specific
#   action   (str)  - "capture" | "list"
_PS_SCRIPT = r"""
param()
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$input_json = [Console]::In.ReadToEnd() | ConvertFrom-Json
$action = $input_json.action

if ($action -eq "list") {
    $result = @()
    $all = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $result += @{
        index  = 0
        left   = $all.X
        top    = $all.Y
        width  = $all.Width
        height = $all.Height
    }
    $idx = 1
    foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
        $b = $s.Bounds
        $result += @{
            index  = $idx
            left   = $b.X
            top    = $b.Y
            width  = $b.Width
            height = $b.Height
        }
        $idx++
    }
    $result | ConvertTo-Json -Compress
    exit 0
}

# action = "capture"
$monitor = [int]$input_json.monitor
$screens = [System.Windows.Forms.Screen]::AllScreens

if ($monitor -eq 0) {
    $rect = [System.Windows.Forms.SystemInformation]::VirtualScreen
} elseif ($monitor -le $screens.Length) {
    $rect = $screens[$monitor - 1].Bounds
} else {
    Write-Error "Monitor $monitor not available"
    exit 1
}

$bmp = New-Object System.Drawing.Bitmap($rect.Width, $rect.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Location, [System.Drawing.Point]::Empty, $rect.Size)
$g.Dispose()

$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
$bytes = $ms.ToArray()
$ms.Dispose()

[Convert]::ToBase64String($bytes)
"""


def _wsl_run_ps(action: str, monitor: int = 0) -> str:
    """Run the PowerShell helper and return its stdout."""
    ps = _powershell_path()
    payload = json.dumps({"action": action, "monitor": monitor})
    # Escape single quotes for PowerShell single-quoted string embedding
    payload_escaped = payload.replace("'", "''")
    # Embed the JSON directly in the script (can't use stdin with -Command).
    script_with_input = _PS_SCRIPT.replace(
        "$input_json = [Console]::In.ReadToEnd() | ConvertFrom-Json",
        f"$input_json = '{payload_escaped}' | ConvertFrom-Json",
    )
    result = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", script_with_input],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell screenshot failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _wsl_capture(monitor: int = 0) -> bytes:
    """Capture the Windows desktop from WSL, return raw PNG bytes."""
    b64_str = _wsl_run_ps("capture", monitor)
    # PowerShell may emit line breaks inside the base64 output
    b64_str = b64_str.replace("\r", "").replace("\n", "")
    return base64.b64decode(b64_str)


def _wsl_list_monitors() -> list[dict[str, int]]:
    """List Windows monitors from WSL."""
    raw = _wsl_run_ps("list")
    data = json.loads(raw)
    # PowerShell outputs a single object (not array) when there's one item
    if isinstance(data, dict):
        data = [data]
    return data


# ---------------------------------------------------------------------------
# mss-based capture (native Windows / macOS / Linux)
# ---------------------------------------------------------------------------


def _mss_capture(monitor: int = 0) -> Image.Image:
    """Capture via mss and return a PIL Image."""
    import mss

    with mss.mss() as sct:
        if monitor < 0 or monitor >= len(sct.monitors):
            raise ValueError(
                f"Monitor {monitor} not available. "
                f"Available: 0-{len(sct.monitors) - 1} "
                f"(0 = all monitors combined)"
            )
        raw = sct.grab(sct.monitors[monitor])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _mss_list_monitors() -> list[dict[str, int]]:
    """List monitors via mss."""
    import mss

    with mss.mss() as sct:
        return [
            {
                "index": i,
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
            }
            for i, m in enumerate(sct.monitors)
        ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resize(img: Image.Image, max_width: int) -> Image.Image:
    """Down-scale *img* so its width does not exceed *max_width*."""
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    return img


def _to_png(img: Image.Image) -> bytes:
    """Encode a PIL Image as optimised PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def capture_screenshot(
    monitor: int = 0,
    *,
    max_width: int = 1920,
    quality: int = 80,
) -> bytes:
    """Capture a screenshot and return it as optimised PNG bytes.

    Parameters
    ----------
    monitor:
        Which monitor to capture.  ``0`` means *all* monitors combined
        into one image, ``1`` is the primary monitor, ``2`` the second, etc.
    max_width:
        Down-scale the image so its width does not exceed this value.
        Keeps the aspect ratio.  Set to ``0`` to disable.
    quality:
        JPEG quality (1-100) when encoding.  Only used for JPEG output.

    Returns
    -------
    bytes
        PNG-encoded image data.
    """
    if is_wsl():
        raw_png = _wsl_capture(monitor)
        img = Image.open(io.BytesIO(raw_png))
        img = _resize(img, max_width)
        return _to_png(img)

    img = _mss_capture(monitor)
    img = _resize(img, max_width)
    return _to_png(img)


def capture_screenshot_b64(
    monitor: int = 0,
    *,
    max_width: int = 1920,
    quality: int = 80,
) -> str:
    """Capture a screenshot and return it as a base64-encoded PNG string."""
    raw = capture_screenshot(monitor, max_width=max_width, quality=quality)
    return base64.b64encode(raw).decode("ascii")


def save_screenshot(
    path: str | Path,
    monitor: int = 0,
    *,
    max_width: int = 1920,
    quality: int = 80,
) -> Path:
    """Capture a screenshot and save it to *path*.

    Returns the resolved path.
    """
    path = Path(path)
    data = capture_screenshot(monitor, max_width=max_width, quality=quality)
    path.write_bytes(data)
    return path.resolve()


def list_monitors() -> list[dict[str, int]]:
    """Return a list of available monitors with their geometry.

    Index ``0`` is the virtual "all-monitors" bounding box.
    """
    if is_wsl():
        return _wsl_list_monitors()
    return _mss_list_monitors()
