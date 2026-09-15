import threading
import tkinter as tk

from .chart_data import ChartPoint, ChartSeries, ChartTarget, fetch_chart_series
from .config import AppSettings
from .windowing import Rect, clamp_window_position, snap_window_position, virtual_screen_bounds


POPUP_WIDTH = 360
POPUP_HEIGHT = 220
POPUP_GAP = 8
POPUP_STACK_GAP = 8
MIN_POPUP_WIDTH = 240
MAX_POPUP_WIDTH = 900
CLOSE_SIZE = 22
RESIZE_HANDLE_SIZE = 14
DEFAULT_DAILY_CANDLES = 44
MIN_DAILY_CANDLES = 20
MAX_DAILY_CANDLES = 120
CTRL_MASK = 0x0004


def chart_popup_position(
    root_x: int,
    root_y: int,
    root_w: int,
    _root_h: int,
    popup_w: int,
    popup_h: int,
    bounds: Rect,
    stack_index: int = 0,
) -> tuple[int, int]:
    x = root_x - popup_w - POPUP_GAP
    if x < bounds.x + 20:
        x = root_x + root_w + POPUP_GAP
    y = root_y + max(0, stack_index) * (popup_h + POPUP_STACK_GAP)
    return clamp_window_position(x, y, popup_w, popup_h, bounds)


def move_window_position(
    start_window_x: int,
    start_window_y: int,
    start_pointer_x: int,
    start_pointer_y: int,
    pointer_x: int,
    pointer_y: int,
) -> tuple[int, int]:
    return start_window_x + pointer_x - start_pointer_x, start_window_y + pointer_y - start_pointer_y


def resize_chart_dimensions(
    start_width: int,
    start_height: int,
    dx: int,
    dy: int,
    corner: str,
) -> tuple[int, int, int, int]:
    ratio = start_width / max(1, start_height)
    delta_from_x = dx if "e" in corner else -dx
    delta_from_y = dy * ratio if "s" in corner else -dy * ratio
    delta_width = delta_from_x if abs(delta_from_x) >= abs(delta_from_y) else delta_from_y
    width = max(MIN_POPUP_WIDTH, min(MAX_POPUP_WIDTH, round(start_width + delta_width)))
    height = max(round(MIN_POPUP_WIDTH / ratio), round(width / ratio))
    move_x = start_width - width if "w" in corner else 0
    move_y = start_height - height if "n" in corner else 0
    return width, height, move_x, move_y


def chart_view_count_from_ctrl_wheel(current_count: int, delta: int) -> int:
    step = max(4, round(current_count * 0.16))
    if delta > 0:
        return max(MIN_DAILY_CANDLES, current_count - step)
    return min(MAX_DAILY_CANDLES, current_count + step)


def chart_pan_offset_from_ctrl_drag(start_offset: int, dx: int, candle_width: float, max_offset: int) -> int:
    moved = round(dx / max(1.0, candle_width))
    return max(0, min(max_offset, start_offset + moved))


def visible_daily_range(total_count: int, view_count: int, offset: int) -> tuple[int, int]:
    end = max(0, total_count - max(0, offset))
    start = max(0, end - max(1, view_count))
    return start, end


def visible_daily_points(points: list[ChartPoint], view_count: int, offset: int) -> list[ChartPoint]:
    start, end = visible_daily_range(len(points), view_count, offset)
    return points[start:end]


def moving_average_values(points: list[ChartPoint], period: int) -> list[float | None]:
    values: list[float | None] = []
    running_sum = 0.0
    for index, point in enumerate(points):
        running_sum += point.close
        if index >= period:
            running_sum -= points[index - period].close
        if index + 1 < period:
            values.append(None)
        else:
            values.append(running_sum / period)
    return values


def macd_values(points: list[ChartPoint]) -> list[tuple[float, float, float]]:
    ema12: float | None = None
    ema26: float | None = None
    dea = 0.0
    values: list[tuple[float, float, float]] = []
    for point in points:
        close = point.close
        ema12 = close if ema12 is None else ema12 * 11 / 13 + close * 2 / 13
        ema26 = close if ema26 is None else ema26 * 25 / 27 + close * 2 / 27
        dif = ema12 - ema26
        dea = dea * 8 / 10 + dif * 2 / 10
        values.append((dif, dea, (dif - dea) * 2))
    return values


def intraday_point_percent(point: ChartPoint, percent_base: float) -> float:
    if percent_base <= 0:
        return 0.0
    return (point.close - percent_base) / percent_base * 100


def intraday_percent_range(points: list[ChartPoint], percent_base: float) -> tuple[float, float]:
    percents = [intraday_point_percent(point, percent_base) for point in points if percent_base > 0]
    max_abs = max((abs(value) for value in percents), default=0.0)
    if max_abs <= 10.5:
        return -10.0, 10.0
    limit = float(int(max_abs + 1))
    return -limit, limit


def intraday_x_ratio_from_label(label: str) -> float | None:
    minute = _minute_of_day(label)
    if minute is None:
        return None
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minute <= morning_start:
        return 0.0
    if minute <= morning_end:
        return (minute - morning_start) / 240
    if minute <= afternoon_start:
        return 120 / 240
    if minute <= afternoon_end:
        return (120 + minute - afternoon_start) / 240
    return 1.0


def _is_ctrl_event(event: tk.Event) -> bool:
    return bool(getattr(event, "state", 0) & CTRL_MASK)


class ChartPopup:
    def __init__(self, root: tk.Tk, settings: AppSettings | None = None, save_callback=None) -> None:
        self.root = root
        self.settings = settings
        self.save_callback = save_callback
        self.windows: list[ChartPopupWindow] = []

    def show(self, target: ChartTarget, mode: str) -> None:
        window = ChartPopupWindow(self, target, mode)
        self.windows.append(window)
        window.show()

    def close(self) -> None:
        for window in list(self.windows):
            window.close()
        self.windows.clear()

    def remove_window(self, window: "ChartPopupWindow") -> None:
        if window in self.windows:
            self.windows.remove(window)

    def root_rect(self) -> Rect:
        self.root.update_idletasks()
        return Rect(
            self.root.winfo_x(),
            self.root.winfo_y(),
            max(1, self.root.winfo_width()),
            max(1, self.root.winfo_height()),
        )

    def window_rects(self, exclude: "ChartPopupWindow | None" = None) -> list[tuple["ChartPopupWindow", Rect]]:
        rects: list[tuple[ChartPopupWindow, Rect]] = []
        for window in self.windows:
            if window is exclude or not window.window:
                continue
            rects.append((window, Rect(window.window.winfo_x(), window.window.winfo_y(), window.width, window.height)))
        return rects

    def follow_root_move(self, dx: int, dy: int) -> None:
        if not dx and not dy:
            return
        for window in self.windows:
            window.follow_root_move(dx, dy)

    def snap_to_root(self) -> None:
        for window in self.windows:
            window.snap_to_root_if_near()

    @property
    def width(self) -> int:
        return self.settings.chart_width if self.settings else POPUP_WIDTH

    @property
    def height(self) -> int:
        return self.settings.chart_height if self.settings else POPUP_HEIGHT

    def save_size(self, width: int, height: int) -> None:
        if not self.settings:
            return
        self.settings.chart_width = width
        self.settings.chart_height = height
        if self.save_callback:
            self.save_callback(self.settings)


class ChartPopupWindow:
    def __init__(self, manager: ChartPopup, target: ChartTarget, mode: str) -> None:
        self.manager = manager
        self.root = manager.root
        self.target = target
        self.mode = mode
        self.width = manager.width
        self.height = manager.height
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.resize_start: tuple[int, int, int, int, int, int, str] | None = None
        self.move_start: tuple[int, int, int, int] | None = None
        self.pan_start: tuple[int, int, float, int] | None = None
        self.hover_corner = ""
        self.attached_to_root = False
        self.current_series: ChartSeries | None = None
        self.daily_view_count = DEFAULT_DAILY_CANDLES
        self.daily_offset = 0
        self.show_volume = False
        self.show_macd = False
        self.refresh_job: str | None = None
        self.loading = False

    def show(self) -> None:
        self._create_window()
        self._place_near_root()
        self._draw_message("加载中...")
        self._fetch_series_async()

    def close(self) -> None:
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None
        if self.window:
            self.window.destroy()
        self.window = None
        self.canvas = None
        self.manager.remove_window(self)

    def _fetch_series_async(self) -> None:
        if self.loading or not self.window:
            return
        self.loading = True

        def worker() -> None:
            series = fetch_chart_series(self.target, self.mode)
            try:
                self.root.after(0, lambda: self._show_series(series))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_refresh(self) -> None:
        if not self.window:
            return
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        seconds = max(2, self.manager.settings.refresh_seconds if self.manager.settings else 3)
        self.refresh_job = self.root.after(seconds * 1000, self._fetch_series_async)

    def _create_window(self) -> None:
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.94)
        self.window.configure(bg="#070b12")
        self.canvas = tk.Canvas(
            self.window,
            width=self.width,
            height=self.height,
            bg="#070b12",
            highlightthickness=1,
            highlightbackground="#1f2937",
        )
        self.canvas.pack(fill="both", expand=True)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.canvas.bind("<ButtonPress-1>", self._start_resize_or_close)
        self.canvas.bind("<B1-Motion>", self._on_pointer_drag)
        self.canvas.bind("<ButtonRelease-1>", self._finish_pointer_drag)
        self.canvas.bind("<ButtonPress-3>", self._start_right_action)
        self.canvas.bind("<B3-Motion>", self._on_pointer_drag)
        self.canvas.bind("<ButtonRelease-3>", self._finish_pointer_drag)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Motion>", self._update_cursor)
        self.canvas.bind("<Leave>", self._clear_hover)

    def _place_near_root(self) -> None:
        if not self.window:
            return
        self.root.update_idletasks()
        bounds = virtual_screen_bounds(self.root)
        x, y = chart_popup_position(
            self.root.winfo_x(),
            self.root.winfo_y(),
            max(1, self.root.winfo_width()),
            max(1, self.root.winfo_height()),
            self.width,
            self.height,
            bounds,
            stack_index=max(0, len(self.manager.windows) - 1),
        )
        self.window.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.attached_to_root = True
        self.window.deiconify()
        self.window.lift()

    def _show_series(self, series: ChartSeries) -> None:
        self.loading = False
        if not self.canvas:
            return
        if series.message or not series.points:
            if self.current_series and self.current_series.points:
                self._redraw_current()
                self._schedule_refresh()
                return
            self._draw_message(series.message or "暂无图表数据")
            self._schedule_refresh()
            return
        self.current_series = series
        self.daily_offset = min(self.daily_offset, self._max_daily_offset(series))
        if self.mode == "intraday":
            self._draw_intraday(series)
        else:
            self._draw_daily(series)
        self._schedule_refresh()

    def _draw_message(self, message: str) -> None:
        if not self.canvas:
            return
        self.canvas.delete("all")
        self.canvas.create_text(
            self.width // 2,
            self.height // 2,
            text=message,
            fill="#cbd5e1",
            font=("Microsoft YaHei UI", 10),
        )
        self._draw_controls()

    def _draw_header(self, series: ChartSeries, label: str) -> None:
        if not self.canvas:
            return
        self.canvas.create_text(
            14,
            14,
            text=f"{series.title} {label}",
            anchor="w",
            fill="#dbeafe",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        if label == "日K":
            self._draw_toggle_button("volume", "量", self.show_volume)
            self._draw_toggle_button("macd", "MACD", self.show_macd)

    def _draw_daily(self, series: ChartSeries) -> None:
        if not self.canvas:
            return
        self.canvas.delete("all")
        self._draw_header(series, "日K")
        start, end = visible_daily_range(len(series.points), self.daily_view_count, self.daily_offset)
        points = series.points[start:end]
        if not points:
            self._draw_message("暂无日 K 数据")
            return
        left, top, right, bottom = 24, 34, self.width - 16, self.height - 28
        has_volume = self.show_volume and any(point.volume > 0 for point in points)
        panel_count = (1 if has_volume else 0) + (1 if self.show_macd else 0)
        panel_height = max(34, min(52, (bottom - top) // 4))
        price_bottom = max(top + 50, bottom - panel_count * panel_height)
        self._draw_grid(left, top, right, price_bottom)
        min_value, max_value = _value_range(points)
        step = max(3, (right - left) / max(1, len(points)))
        candle_w = max(2, min(6, step * 0.55))
        for index, point in enumerate(points):
            x = left + step * index + step / 2
            y_open = _scale_y(point.open, min_value, max_value, top, price_bottom)
            y_close = _scale_y(point.close, min_value, max_value, top, price_bottom)
            y_high = _scale_y(point.high, min_value, max_value, top, price_bottom)
            y_low = _scale_y(point.low, min_value, max_value, top, price_bottom)
            color = "#ef4444" if point.close >= point.open else "#22c55e"
            self.canvas.create_line(x, y_high, x, y_low, fill=color)
            self.canvas.create_rectangle(
                x - candle_w / 2,
                min(y_open, y_close),
                x + candle_w / 2,
                max(y_open, y_close) + 1,
                outline=color,
                fill=color,
            )
        self._draw_ma_lines(series.points, start, end, left, top, right, price_bottom, min_value, max_value)
        self._draw_value_labels(min_value, max_value, top, price_bottom, right)
        next_top = price_bottom
        if has_volume:
            volume_bottom = next_top + panel_height
            self._draw_volume_panel(points, left, next_top, right, volume_bottom, step, candle_w)
            next_top = volume_bottom
        if self.show_macd:
            self._draw_macd_panel(series.points, start, end, left, next_top, right, bottom)
        self.canvas.create_text(
            left,
            bottom + 14,
            text=f"{points[0].label}  {points[-1].label}",
            anchor="w",
            fill="#64748b",
            font=("Consolas", 8),
        )
        self._draw_controls()

    def _draw_intraday(self, series: ChartSeries) -> None:
        if not self.canvas:
            return
        self.canvas.delete("all")
        self._draw_header(series, "分时")
        points = series.points
        left, top, right, bottom = 44, 34, self.width - 16, self.height - 28
        self._draw_frame(left, top, right, bottom)
        percent_base = series.percent_base or points[0].close
        min_percent, max_percent = intraday_percent_range(points, percent_base)
        zero_y = _scale_y(0.0, min_percent, max_percent, top, bottom)
        self.canvas.create_line(left, zero_y, right, zero_y, fill="#334155", width=1)
        coords: list[float] = []
        for index, point in enumerate(points):
            ratio = intraday_x_ratio_from_label(point.label)
            if ratio is None:
                ratio = index / max(1, len(points) - 1)
            x = left + (right - left) * ratio
            y = _scale_y(intraday_point_percent(point, percent_base), min_percent, max_percent, top, bottom)
            coords.extend([x, y])
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill="#38bdf8", width=2, smooth=True)
        latest = points[-1].close
        latest_percent = intraday_point_percent(points[-1], percent_base)
        self.canvas.create_text(
            left,
            bottom + 16,
            text=f"09:30  15:00  最新 {latest:.2f}  {latest_percent:+.2f}%",
            anchor="w",
            fill="#94a3b8",
            font=("Consolas", 8),
        )
        self._draw_percent_labels(min_percent, max_percent, top, bottom, left)
        self._draw_controls()

    def _draw_grid(self, left: int, top: int, right: int, bottom: int) -> None:
        if not self.canvas:
            return
        for i in range(4):
            y = top + (bottom - top) * i / 3
            self.canvas.create_line(left, y, right, y, fill="#162033")
        self.canvas.create_rectangle(left, top, right, bottom, outline="#243244")

    def _draw_frame(self, left: int, top: int, right: int, bottom: int) -> None:
        if not self.canvas:
            return
        self.canvas.create_rectangle(left, top, right, bottom, outline="#243244")

    def _draw_value_labels(self, min_value: float, max_value: float, top: int, bottom: int, right: int) -> None:
        if not self.canvas:
            return
        self.canvas.create_text(right, top - 10, text=f"{max_value:.2f}", anchor="e", fill="#64748b", font=("Consolas", 8))
        self.canvas.create_text(right, bottom + 10, text=f"{min_value:.2f}", anchor="e", fill="#64748b", font=("Consolas", 8))

    def _draw_percent_labels(self, min_percent: float, max_percent: float, top: int, bottom: int, left: int) -> None:
        if not self.canvas:
            return
        zero_y = _scale_y(0.0, min_percent, max_percent, top, bottom)
        self.canvas.create_text(left - 4, top, text=f"{max_percent:+.0f}%", anchor="e", fill="#64748b", font=("Consolas", 8))
        self.canvas.create_text(left - 4, zero_y, text="0%", anchor="e", fill="#64748b", font=("Consolas", 8))
        self.canvas.create_text(left - 4, bottom, text=f"{min_percent:+.0f}%", anchor="e", fill="#64748b", font=("Consolas", 8))

    def _draw_ma_lines(
        self,
        all_points: list[ChartPoint],
        start: int,
        end: int,
        left: int,
        top: int,
        right: int,
        bottom: int,
        min_value: float,
        max_value: float,
    ) -> None:
        if not self.canvas:
            return
        self._draw_indicator_line(moving_average_values(all_points, 5)[start:end], left, top, right, bottom, min_value, max_value, "#facc15")
        self._draw_indicator_line(moving_average_values(all_points, 10)[start:end], left, top, right, bottom, min_value, max_value, "#38bdf8")
        self.canvas.create_text(left, top - 10, text="MA5", anchor="w", fill="#facc15", font=("Consolas", 8))
        self.canvas.create_text(left + 34, top - 10, text="MA10", anchor="w", fill="#38bdf8", font=("Consolas", 8))

    def _draw_indicator_line(
        self,
        values: list[float | None],
        left: int,
        top: int,
        right: int,
        bottom: int,
        min_value: float,
        max_value: float,
        color: str,
    ) -> None:
        if not self.canvas:
            return
        coords: list[float] = []
        for index, value in enumerate(values):
            if value is None:
                if len(coords) >= 4:
                    self.canvas.create_line(*coords, fill=color, width=1)
                coords = []
                continue
            x = left + (right - left) * (index + 0.5) / max(1, len(values))
            y = _scale_y(value, min_value, max_value, top, bottom)
            coords.extend([x, y])
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill=color, width=1)

    def _draw_volume_panel(
        self,
        points: list[ChartPoint],
        left: int,
        top: int,
        right: int,
        bottom: int,
        step: float,
        candle_w: float,
    ) -> None:
        if not self.canvas:
            return
        self._draw_grid(left, top, right, bottom)
        max_volume = max((point.volume for point in points), default=0.0)
        if max_volume <= 0:
            self.canvas.create_text(left, (top + bottom) / 2, text="暂无成交量", anchor="w", fill="#64748b", font=("Microsoft YaHei UI", 8))
            return
        for index, point in enumerate(points):
            x = left + step * index + step / 2
            bar_top = bottom - (point.volume / max_volume) * max(1, bottom - top - 4)
            color = "#ef4444" if point.close >= point.open else "#22c55e"
            self.canvas.create_rectangle(x - candle_w / 2, bar_top, x + candle_w / 2, bottom, outline=color, fill=color)
        self.canvas.create_text(left, top + 8, text="VOL", anchor="w", fill="#94a3b8", font=("Consolas", 8))

    def _draw_macd_panel(
        self,
        all_points: list[ChartPoint],
        start: int,
        end: int,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        if not self.canvas:
            return
        values = macd_values(all_points)[start:end]
        if not values:
            return
        self._draw_grid(left, top, right, bottom)
        macd_min = min(min(dif, dea, bar) for dif, dea, bar in values)
        macd_max = max(max(dif, dea, bar) for dif, dea, bar in values)
        if macd_min == macd_max:
            macd_min -= 0.01
            macd_max += 0.01
        zero_y = _scale_y(0, macd_min, macd_max, top, bottom)
        self.canvas.create_line(left, zero_y, right, zero_y, fill="#334155")
        step = max(3, (right - left) / max(1, len(values)))
        bar_w = max(1, min(5, step * 0.45))
        dif_values: list[float] = []
        dea_values: list[float] = []
        for index, (dif, dea, bar) in enumerate(values):
            x = left + step * index + step / 2
            y = _scale_y(bar, macd_min, macd_max, top, bottom)
            color = "#ef4444" if bar >= 0 else "#22c55e"
            self.canvas.create_rectangle(x - bar_w / 2, min(y, zero_y), x + bar_w / 2, max(y, zero_y), outline=color, fill=color)
            dif_values.append(dif)
            dea_values.append(dea)
        self._draw_indicator_line(dif_values, left, top, right, bottom, macd_min, macd_max, "#facc15")
        self._draw_indicator_line(dea_values, left, top, right, bottom, macd_min, macd_max, "#38bdf8")
        self.canvas.create_text(left, top + 8, text="MACD", anchor="w", fill="#94a3b8", font=("Consolas", 8))

    def _draw_controls(self) -> None:
        if self.canvas:
            self.canvas.delete("control")
        self._draw_close_button()

    def _draw_toggle_button(self, name: str, text: str, active: bool) -> None:
        if not self.canvas:
            return
        x1, y1, x2, y2 = self._toggle_button_rect(name)
        self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            fill="#1e293b" if active else "#020617",
            outline="#60a5fa" if active else "#334155",
            tags=("chart_toggle", f"toggle_{name}"),
        )
        self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=text,
            fill="#dbeafe" if active else "#94a3b8",
            font=("Microsoft YaHei UI", 8, "bold"),
            tags=("chart_toggle", f"toggle_{name}"),
        )

    def _toggle_button_rect(self, name: str) -> tuple[int, int, int, int]:
        close_left = self.width - CLOSE_SIZE - 4
        if name == "macd":
            return close_left - 52, 5, close_left - 6, 24
        return close_left - 80, 5, close_left - 56, 24

    def _draw_close_button(self) -> None:
        if not self.canvas:
            return
        x1 = self.width - CLOSE_SIZE - 4
        y1 = 4
        x2 = self.width - 4
        y2 = CLOSE_SIZE + 4
        self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            fill="#7f1d1d",
            outline="#fca5a5",
            tags=("control", "close_button"),
        )
        self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2 - 1,
            text="X",
            fill="#ffffff",
            font=("Consolas", 12, "bold"),
            tags=("control", "close_button"),
        )

    def _start_resize_or_close(self, event: tk.Event) -> None:
        if self._is_close_event(event.x, event.y):
            self.close()
            return
        if self.mode == "daily" and self._is_toggle_event(event.x, event.y, "volume"):
            self.show_volume = not self.show_volume
            self._redraw_current()
            return
        if self.mode == "daily" and self._is_toggle_event(event.x, event.y, "macd"):
            self.show_macd = not self.show_macd
            self._redraw_current()
            return
        corner = self._resize_corner_at(event.x, event.y)
        if not self.window:
            return
        if corner:
            self.move_start = None
            self.resize_start = (
                event.x_root,
                event.y_root,
                self.width,
                self.height,
                self.window.winfo_x(),
                self.window.winfo_y(),
                corner,
            )
            return
        self._start_move(event)

    def _start_right_action(self, event: tk.Event) -> None:
        if self.mode == "daily" and self.current_series and _is_ctrl_event(event):
            candle_width = self._daily_candle_width()
            self.pan_start = (event.x_root, self.daily_offset, candle_width, self._max_daily_offset(self.current_series))
            self.resize_start = None
            self.move_start = None
            return
        self._start_move(event)

    def _start_move(self, event: tk.Event) -> None:
        if not self.window:
            return
        self.resize_start = None
        self.pan_start = None
        self.attached_to_root = False
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _on_pointer_drag(self, event: tk.Event) -> None:
        if self.resize_start:
            self._resize_from_event(event)
            return
        if self.pan_start and self.current_series:
            start_x, start_offset, candle_width, max_offset = self.pan_start
            self.daily_offset = chart_pan_offset_from_ctrl_drag(start_offset, event.x_root - start_x, candle_width, max_offset)
            self._redraw_current()
            return
        if self.move_start and self.window:
            start_x, start_y, window_x, window_y = self.move_start
            x, y = move_window_position(window_x, window_y, start_x, start_y, event.x_root, event.y_root)
            self.window.geometry(f"+{x}+{y}")

    def _on_mouse_wheel(self, event: tk.Event) -> str | None:
        if self.mode != "daily" or not self.current_series or not _is_ctrl_event(event):
            return None
        old_count = self.daily_view_count
        self.daily_view_count = chart_view_count_from_ctrl_wheel(self.daily_view_count, event.delta)
        if self.daily_view_count != old_count:
            self.daily_offset = min(self.daily_offset, self._max_daily_offset(self.current_series))
            self._redraw_current()
        return "break"

    def _resize_from_event(self, event: tk.Event) -> None:
        if not self.resize_start or not self.window or not self.canvas:
            return
        start_x, start_y, start_width, start_height, window_x, window_y, corner = self.resize_start
        width, height, move_x, move_y = resize_chart_dimensions(
            start_width,
            start_height,
            event.x_root - start_x,
            event.y_root - start_y,
            corner,
        )
        self.width = width
        self.height = height
        self.canvas.configure(width=width, height=height)
        self.window.geometry(f"{width}x{height}+{window_x + move_x}+{window_y + move_y}")
        self._redraw_current()

    def _finish_pointer_drag(self, _event: tk.Event) -> None:
        if self.resize_start:
            self.resize_start = None
            self.manager.save_size(self.width, self.height)
            self._snap_after_drag()
        elif self.pan_start:
            self.pan_start = None
        elif self.move_start:
            self._snap_after_drag()
        self.move_start = None

    def _update_cursor(self, event: tk.Event) -> None:
        if not self.canvas:
            return
        corner = self._resize_corner_at(event.x, event.y)
        if self._is_close_event(event.x, event.y):
            self.canvas.configure(cursor="hand2")
        elif self.mode == "daily" and (self._is_toggle_event(event.x, event.y, "volume") or self._is_toggle_event(event.x, event.y, "macd")):
            self.canvas.configure(cursor="hand2")
        elif corner:
            self.canvas.configure(cursor="sizing")
        else:
            self.canvas.configure(cursor="fleur")
        if corner != self.hover_corner:
            self.hover_corner = corner
            self._draw_controls()

    def _clear_hover(self, _event: tk.Event) -> None:
        if self.canvas:
            self.canvas.configure(cursor="")
        if self.hover_corner:
            self.hover_corner = ""
            self._draw_controls()

    def _snap_after_drag(self) -> None:
        if not self.window:
            return
        x = self.window.winfo_x()
        y = self.window.winfo_y()
        attached = False
        root_rect = self.manager.root_rect()
        snapped_x, snapped_y = snap_window_position(x, y, self.width, self.height, [root_rect])
        if (snapped_x, snapped_y) != (x, y):
            x, y = snapped_x, snapped_y
            attached = True
        else:
            for other, rect in self.manager.window_rects(exclude=self):
                snapped_x, snapped_y = snap_window_position(x, y, self.width, self.height, [rect])
                if (snapped_x, snapped_y) != (x, y):
                    x, y = snapped_x, snapped_y
                    attached = other.attached_to_root
                    break
        self.attached_to_root = attached
        self.window.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def follow_root_move(self, dx: int, dy: int) -> None:
        if not self.attached_to_root or not self.window:
            return
        self.window.geometry(f"{self.width}x{self.height}+{self.window.winfo_x() + dx}+{self.window.winfo_y() + dy}")

    def snap_to_root_if_near(self) -> None:
        if not self.window:
            return
        x = self.window.winfo_x()
        y = self.window.winfo_y()
        snapped_x, snapped_y = snap_window_position(x, y, self.width, self.height, [self.manager.root_rect()])
        if (snapped_x, snapped_y) == (x, y):
            return
        self.attached_to_root = True
        self.window.geometry(f"{self.width}x{self.height}+{snapped_x}+{snapped_y}")

    def _redraw_current(self) -> None:
        if not self.current_series:
            self._draw_message("加载中...")
            return
        if self.mode == "intraday":
            self._draw_intraday(self.current_series)
        else:
            self._draw_daily(self.current_series)

    def _resize_corner_at(self, x: int, y: int) -> str:
        size = RESIZE_HANDLE_SIZE
        if x <= size and y <= size:
            return "nw"
        if x >= self.width - size and y <= size:
            return "ne"
        if x <= size and y >= self.height - size:
            return "sw"
        if x >= self.width - size and y >= self.height - size:
            return "se"
        return ""

    def _is_close_event(self, x: int, y: int) -> bool:
        return self.width - CLOSE_SIZE - 4 <= x <= self.width - 4 and 4 <= y <= CLOSE_SIZE + 4

    def _is_toggle_event(self, x: int, y: int, name: str) -> bool:
        x1, y1, x2, y2 = self._toggle_button_rect(name)
        return x1 <= x <= x2 and y1 <= y <= y2

    def _max_daily_offset(self, series: ChartSeries) -> int:
        return max(0, len(series.points) - self.daily_view_count)

    def _daily_candle_width(self) -> float:
        left, right = 24, self.width - 16
        return (right - left) / max(1, self.daily_view_count)


def _value_range(points: list[ChartPoint]) -> tuple[float, float]:
    values: list[float] = []
    for point in points:
        values.extend([point.open, point.close, point.high, point.low])
    min_value = min(values)
    max_value = max(values)
    if min_value == max_value:
        padding = max(0.01, abs(min_value) * 0.002)
        return min_value - padding, max_value + padding
    padding = (max_value - min_value) * 0.08
    return min_value - padding, max_value + padding


def _scale_y(value: float, min_value: float, max_value: float, top: int, bottom: int) -> float:
    return bottom - (value - min_value) / (max_value - min_value) * (bottom - top)


def _minute_of_day(label: str) -> int | None:
    value = str(label).strip()
    if ":" in value:
        hour_text, minute_text = value[-5:].split(":", 1)
    elif len(value) >= 4 and value[-4:].isdigit():
        hour_text, minute_text = value[-4:-2], value[-2:]
    else:
        return None
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute
