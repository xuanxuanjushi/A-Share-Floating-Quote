import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path


_INSTANCE_MUTEX = None
_INSTANCE_LOCK_FILE = None


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def enable_windows_dpi_awareness() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def acquire_single_instance(name: str) -> bool:
    global _INSTANCE_MUTEX, _INSTANCE_LOCK_FILE
    if not sys.platform.startswith("win"):
        return True
    try:
        import msvcrt

        lock_dir = Path(os.environ.get("APPDATA", Path.home())) / "AStockFloater"
        lock_dir.mkdir(parents=True, exist_ok=True)
        _INSTANCE_LOCK_FILE = (lock_dir / "app.lock").open("a+b")
        try:
            msvcrt.locking(_INSTANCE_LOCK_FILE.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            _INSTANCE_LOCK_FILE.close()
            _INSTANCE_LOCK_FILE = None
            return False
    except OSError:
        pass
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return False
        _INSTANCE_MUTEX = handle
    except (AttributeError, OSError):
        return True
    return True


def virtual_screen_bounds(root) -> Rect:
    if sys.platform.startswith("win"):
        try:
            user32 = ctypes.windll.user32
            x = int(user32.GetSystemMetrics(76))
            y = int(user32.GetSystemMetrics(77))
            width = int(user32.GetSystemMetrics(78))
            height = int(user32.GetSystemMetrics(79))
            if width > 0 and height > 0:
                return Rect(x, y, width, height)
        except (AttributeError, OSError):
            pass
    return Rect(0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight()))


def clamp_window_position(
    x: int,
    y: int,
    window_width: int,
    window_height: int,
    bounds: Rect,
    padding: int = 20,
) -> tuple[int, int]:
    min_x = bounds.x + padding
    min_y = bounds.y + padding
    max_x = bounds.right - padding - max(1, window_width)
    max_y = bounds.bottom - padding - max(1, window_height)

    clamped_x = min_x if max_x < min_x else min(max(x, min_x), max_x)
    clamped_y = min_y if max_y < min_y else min(max(y, min_y), max_y)
    return clamped_x, clamped_y


def snap_window_position(
    x: int,
    y: int,
    window_width: int,
    window_height: int,
    neighbors: list[Rect],
    gap: int = 8,
    threshold: int = 16,
) -> tuple[int, int]:
    snapped_x = x
    snapped_y = y
    window_right = x + window_width
    window_bottom = y + window_height

    for neighbor in neighbors:
        vertically_close = window_bottom >= neighbor.y - threshold and y <= neighbor.bottom + threshold
        horizontally_close = window_right >= neighbor.x - threshold and x <= neighbor.right + threshold

        if vertically_close:
            if abs(window_right - (neighbor.x - gap)) <= threshold:
                snapped_x = neighbor.x - gap - window_width
            elif abs(x - (neighbor.right + gap)) <= threshold:
                snapped_x = neighbor.right + gap

        if horizontally_close:
            if abs(window_bottom - (neighbor.y - gap)) <= threshold:
                snapped_y = neighbor.y - gap - window_height
            elif abs(y - (neighbor.bottom + gap)) <= threshold:
                snapped_y = neighbor.bottom + gap

        if abs(y - neighbor.y) <= threshold:
            snapped_y = neighbor.y
        elif abs(window_bottom - neighbor.bottom) <= threshold:
            snapped_y = neighbor.bottom - window_height

        if abs(x - neighbor.x) <= threshold:
            snapped_x = neighbor.x
        elif abs(window_right - neighbor.right) <= threshold:
            snapped_x = neighbor.right - window_width

    return snapped_x, snapped_y
