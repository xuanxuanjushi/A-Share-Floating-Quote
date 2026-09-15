import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .chart_data import (
    ChartTarget,
    board_chart_target,
    market_chart_target,
    stock_chart_target,
)
from .chart_popup import ChartPopup
from .config import (
    AppSettings,
    BoardItem,
    StockItem,
    export_settings,
    import_settings,
    load_settings,
    normalize_board_code,
    normalize_stock_code,
    save_settings,
)
from .market_data import (
    BoardQuote,
    MARKET_INDICATORS,
    MarketIndicator,
    StockQuote,
    fetch_board_quotes,
    fetch_market_indicators,
    fetch_stock_quotes,
)
from .quote_cache import merge_quotes_with_cache
from .windowing import clamp_window_position, virtual_screen_bounds


MIN_MAIN_SCALE = 0.5
MAX_MAIN_SCALE = 1.8
MAIN_RESIZE_HANDLE = 14
HIDDEN_DIALOG_X = -32000
HIDDEN_DIALOG_Y = -32000


def opacity_from_middle_drag(start_opacity: float, dy: int) -> float:
    return round(max(0.35, min(1.0, start_opacity - dy * 0.002)), 2)


def scale_from_corner_drag(
    start_scale: float,
    start_width: int,
    start_height: int,
    dx: int,
    dy: int,
    corner: str,
) -> float:
    width_delta = dx if "e" in corner else -dx
    height_delta = dy if "s" in corner else -dy
    width_ratio = width_delta / max(1, start_width)
    height_ratio = height_delta / max(1, start_height)
    ratio = width_ratio if abs(width_ratio) >= abs(height_ratio) else height_ratio
    return round(max(MIN_MAIN_SCALE, min(MAX_MAIN_SCALE, start_scale * (1 + ratio))), 2)


def prepare_dialog_for_hidden_layout(dialog: tk.Toplevel, width: int, height: int) -> None:
    dialog.withdraw()
    try:
        dialog.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    dialog.geometry(f"{width}x{height}+{HIDDEN_DIALOG_X}+{HIDDEN_DIALOG_Y}")


def reveal_prepared_dialog(dialog: tk.Toplevel) -> None:
    try:
        dialog.attributes("-alpha", 1.0)
    except tk.TclError:
        pass


class StockFloaterApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.icon_path = self._resource_path("assets/app_icon.ico")
        self.root = tk.Tk()
        self.root.title("小窗")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.settings.opacity)
        self.root.configure(bg="#0b0d0f")
        self.root.geometry(f"+{self.settings.window_x}+{self.settings.window_y}")
        self._apply_window_icon(self.root)

        self.quote_queue: queue.Queue[
            tuple[dict[str, StockQuote], dict[str, BoardQuote], dict[str, MarketIndicator]]
        ] = queue.Queue()
        self.row_widgets: list[dict[str, object]] = []
        self.drag_start: tuple[int, int, int, int] | None = None
        self.drag_moved = False
        self.opacity_drag_start: tuple[int, float] | None = None
        self.scale_drag_start: tuple[int, int, float, int, int, int, int, str] | None = None
        self.refresh_job: str | None = None
        self.refresh_inflight = False
        self.last_stock_quotes: dict[str, StockQuote] = {}
        self.last_board_quotes: dict[str, BoardQuote] = {}
        self.last_market_quotes: dict[str, MarketIndicator] = {}
        self.row_layout_signature: tuple | None = None
        self.chart_popup = ChartPopup(self.root, self.settings, save_settings)

        self.container = tk.Frame(
            self.root,
            bg="#0b0d0f",
            padx=self._scaled(6),
            pady=self._scaled(4),
        )
        self.container.pack(fill="both", expand=True)
        self._bind_middle_opacity(self.container)
        self._bind_main_resize(self.container)

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="设置", command=self.open_settings)
        self.menu.add_command(label="缩放", command=self.open_scale)
        self.menu.add_command(label="透明度", command=self.open_opacity)
        self.menu.add_separator()
        self.menu.add_command(label="立即刷新", command=self.refresh_quotes)
        self.menu.add_command(label="回到屏幕内", command=self.reset_window_position)
        self.menu.add_command(label="退出", command=self.close)

        self.root.bind("<ButtonPress-3>", self.start_right_drag)
        self.root.bind("<B3-Motion>", self.on_right_drag)
        self.root.bind("<ButtonRelease-3>", self.finish_right_drag)
        self._bind_middle_opacity(self.root)
        self._bind_main_resize(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def run(self) -> None:
        self.render_rows({})
        self._ensure_root_visible(save=True)
        if not self._visible_items() and not self._visible_boards():
            self.root.after(300, self.open_settings)
        self.refresh_quotes()
        self.root.mainloop()

    def render_rows(self, quotes: dict[str, StockQuote]) -> None:
        self.render_all_rows(quotes or self.last_stock_quotes, self.last_board_quotes, self.last_market_quotes)

    def render_all_rows(
        self,
        quotes: dict[str, StockQuote],
        board_quotes: dict[str, BoardQuote],
        market_quotes: dict[str, MarketIndicator],
    ) -> None:
        stocks = self._visible_items()
        boards = self._visible_boards()
        market_keys = self._visible_market_keys()
        rows = self._build_render_rows(stocks, boards, market_keys, quotes, board_quotes, market_quotes)
        layout_signature = self._row_layout_signature(rows)
        if layout_signature != self.row_layout_signature:
            self._rebuild_render_rows(rows)
            self.row_layout_signature = layout_signature
        else:
            self._update_render_rows(rows)
        self.root.update_idletasks()

    def _build_render_rows(
        self,
        stocks: list[StockItem],
        boards: list[BoardItem],
        market_keys: list[str],
        quotes: dict[str, StockQuote],
        board_quotes: dict[str, BoardQuote],
        market_quotes: dict[str, MarketIndicator],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if not stocks and not boards and not market_keys:
            return [{"kind": "empty", "key": "empty", "text": "右键设置"}]

        for index, stock in enumerate(stocks, start=1):
            stock_key = self._stock_key(stock)
            quote = quotes.get(stock_key)
            percent_text, fg = self._percent_display(quote)
            rows.append(
                {
                    "kind": "quote",
                    "key": f"stock:{index}:{stock_key}",
                    "name": self._display_name(index, stock, quote),
                    "price_text": self._price_display(quote, stock),
                    "percent_text": percent_text,
                    "percent_fg": fg,
                    "item": stock,
                    "chart_target": stock_chart_target(stock, quote),
                }
            )

        for key in market_keys:
            quote = market_quotes.get(key)
            name = self._market_display_name(key, quote)
            percent_text, fg = self._percent_display(quote)
            rows.append(
                {
                    "kind": "market",
                    "key": f"market:{key}",
                    "name": name,
                    "price_text": self._market_price_display(quote),
                    "percent_text": percent_text,
                    "percent_fg": fg,
                    "chart_target": market_chart_target(key, name),
                }
            )

        if boards:
            rows.append({"kind": "section", "key": "section:boards", "text": "板块"})
        for index, board in enumerate(boards, start=1):
            board_key = self._board_key(board)
            quote = board_quotes.get(board_key)
            percent_text, fg = self._percent_display(quote)
            rows.append(
                {
                    "kind": "quote",
                    "key": f"board:{index}:{board_key}",
                    "name": self._display_board_name(index, board, quote),
                    "price_text": self._price_display(quote, board),
                    "percent_text": percent_text,
                    "percent_fg": fg,
                    "item": board,
                    "chart_target": board_chart_target(board, quote),
                }
            )
        return rows

    def _row_layout_signature(self, rows: list[dict[str, object]]) -> tuple:
        return tuple((row["kind"], row["key"], bool(row.get("price_text"))) for row in rows)

    def _rebuild_render_rows(self, rows: list[dict[str, object]]) -> None:
        for widgets in self.row_widgets:
            root = widgets.get("root")
            if isinstance(root, tk.Widget):
                root.destroy()
        self.row_widgets.clear()

        for row_data in rows:
            kind = row_data["kind"]
            if kind == "section":
                self.row_widgets.append(self._create_section_row(row_data))
            elif kind == "empty":
                self.row_widgets.append(self._create_empty_row(row_data))
            elif kind == "market":
                self.row_widgets.append(self._create_market_row(row_data))
            else:
                self.row_widgets.append(self._create_quote_row(row_data))
        self._update_render_rows(rows)

    def _update_render_rows(self, rows: list[dict[str, object]]) -> None:
        for widgets, row_data in zip(self.row_widgets, rows):
            kind = row_data["kind"]
            if kind in {"section", "empty"}:
                label = widgets.get("root")
                if isinstance(label, tk.Label):
                    self._configure_label(label, text=str(row_data["text"]))
                continue

            name_label = widgets.get("name_label")
            if isinstance(name_label, tk.Label):
                self._configure_label(
                    name_label,
                    text=str(row_data["name"]),
                    fg="#8fa6b8" if len(str(row_data["name"])) <= 2 else "#aeb8bf",
                )
                item = row_data.get("item")
                if isinstance(item, (StockItem, BoardItem)):
                    name_label.bind("<Button-1>", lambda _event, current=item: self.toggle_name(current))

            price_label = widgets.get("price_label")
            if isinstance(price_label, tk.Label):
                self._configure_label(price_label, text=str(row_data["price_text"]))

            percent_label = widgets.get("percent_label")
            if isinstance(percent_label, tk.Label):
                self._configure_label(
                    percent_label,
                    text=str(row_data["percent_text"]),
                    fg=str(row_data["percent_fg"]),
                )

            target = row_data.get("chart_target")
            if isinstance(target, ChartTarget):
                self._bind_chart_labels(widgets, target)

    def _configure_label(self, label: tk.Label, **options: object) -> None:
        changed = {key: value for key, value in options.items() if str(label.cget(key)) != str(value)}
        if changed:
            label.configure(**changed)

    def _bind_row_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-3>", self.start_right_drag)
        widget.bind("<B3-Motion>", self.on_right_drag)
        widget.bind("<ButtonRelease-3>", self.finish_right_drag)
        self._bind_middle_opacity(widget)
        self._bind_main_resize(widget)

    def _bind_middle_opacity(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-2>", self.start_middle_opacity)
        widget.bind("<B2-Motion>", self.on_middle_opacity)
        widget.bind("<ButtonRelease-2>", self.finish_middle_opacity)

    def _bind_main_resize(self, widget: tk.Widget) -> None:
        if not hasattr(widget, "_stock_default_cursor"):
            setattr(widget, "_stock_default_cursor", str(widget.cget("cursor")))
        widget.bind("<ButtonPress-1>", self.start_main_resize, add="+")
        widget.bind("<B1-Motion>", self.on_main_resize, add="+")
        widget.bind("<ButtonRelease-1>", self.finish_main_resize, add="+")
        widget.bind("<Motion>", self.update_main_resize_cursor, add="+")
        widget.bind("<Leave>", self.clear_main_resize_cursor, add="+")

    def _bind_chart_labels(self, widgets: dict[str, object], target: ChartTarget) -> None:
        price_label = widgets.get("price_label")
        if isinstance(price_label, tk.Label):
            price_label.configure(cursor="hand2")
            setattr(price_label, "_stock_default_cursor", "hand2")
            price_label.bind("<Button-1>", lambda _event, current=target: self.show_chart(current, "daily"))

        percent_label = widgets.get("percent_label")
        if isinstance(percent_label, tk.Label):
            percent_label.configure(cursor="hand2")
            setattr(percent_label, "_stock_default_cursor", "hand2")
            percent_label.bind("<Button-1>", lambda _event, current=target: self.show_chart(current, "intraday"))

    def show_chart(self, target: ChartTarget, mode: str) -> None:
        self.chart_popup.show(target, mode)

    def _create_section_row(self, row_data: dict[str, object]) -> dict[str, object]:
        label = tk.Label(
            self.container,
            text=str(row_data["text"]),
            fg="#8f8f8f",
            bg="#0b0d0f",
            font=self._font(7),
        )
        label.pack(anchor="w", fill="x", pady=(3, 0))
        self._bind_row_drag(label)
        return {"root": label}

    def _create_quote_row(self, row_data: dict[str, object]) -> dict[str, object]:
        price_text = str(row_data["price_text"])
        row = tk.Frame(self.container, bg="#0b0d0f")
        row.pack(anchor="w", fill="x", pady=self._scaled(1))
        row.columnconfigure(1, minsize=self._scaled(52 if price_text else 56))
        if price_text:
            row.columnconfigure(2, minsize=self._scaled(56))
        self._bind_row_drag(row)

        name_label = tk.Label(
            row,
            text=str(row_data["name"]),
            fg="#8fa6b8" if len(str(row_data["name"])) <= 2 else "#aeb8bf",
            bg="#0b0d0f",
            font=self._font(9),
            cursor="hand2",
        )
        name_label.grid(row=0, column=0, sticky="w", padx=(0, self._scaled(8)))
        item = row_data.get("item")
        if isinstance(item, (StockItem, BoardItem)):
            name_label.bind("<Button-1>", lambda _event, current=item: self.toggle_name(current))
        self._bind_row_drag(name_label)

        percent_column = 1
        widgets: dict[str, object] = {"root": row, "name_label": name_label}
        if price_text:
            price_label = tk.Label(
                row,
                text=price_text,
                fg="#8d979e",
                bg="#0b0d0f",
                font=self._font(8, "Consolas"),
                width=7,
                anchor="e",
                cursor="hand2",
            )
            price_label.grid(row=0, column=1, sticky="e", padx=(0, self._scaled(6)))
            self._bind_row_drag(price_label)
            widgets["price_label"] = price_label
            percent_column = 2

        percent_label = tk.Label(
            row,
            text=str(row_data["percent_text"]),
            fg=str(row_data["percent_fg"]),
            bg="#0b0d0f",
            font=self._font(9, "Consolas", "bold"),
            width=8,
            anchor="e",
            cursor="hand2",
        )
        percent_label.grid(row=0, column=percent_column, sticky="e")
        self._bind_row_drag(percent_label)
        widgets["percent_label"] = percent_label
        return widgets

    def _create_market_row(self, row_data: dict[str, object]) -> dict[str, object]:
        row = tk.Frame(self.container, bg="#0b0d0f")
        row.pack(anchor="w", fill="x", pady=self._scaled(1))
        row.columnconfigure(1, minsize=self._scaled(52))
        row.columnconfigure(2, minsize=self._scaled(56))
        self._bind_row_drag(row)

        name_label = tk.Label(
            row,
            text=str(row_data["name"]),
            fg="#aeb8bf",
            bg="#0b0d0f",
            font=self._font(9),
        )
        name_label.grid(row=0, column=0, sticky="w", padx=(0, self._scaled(8)))
        self._bind_row_drag(name_label)

        price_label = tk.Label(
            row,
            text=str(row_data["price_text"]),
            fg="#8d979e",
            bg="#0b0d0f",
            font=self._font(8, "Consolas"),
            width=7,
            anchor="e",
            cursor="hand2",
        )
        price_label.grid(row=0, column=1, sticky="e", padx=(0, self._scaled(6)))
        self._bind_row_drag(price_label)

        percent_label = tk.Label(
            row,
            text=str(row_data["percent_text"]),
            fg=str(row_data["percent_fg"]),
            bg="#0b0d0f",
            font=self._font(9, "Consolas", "bold"),
            width=8,
            anchor="e",
            cursor="hand2",
        )
        percent_label.grid(row=0, column=2, sticky="e")
        self._bind_row_drag(percent_label)
        return {
            "root": row,
            "name_label": name_label,
            "price_label": price_label,
            "percent_label": percent_label,
        }

    def _create_empty_row(self, row_data: dict[str, object]) -> dict[str, object]:
        label = tk.Label(
            self.container,
            text=str(row_data["text"]),
            fg="#cfcfcf",
            bg="#0b0d0f",
            font=self._font(9),
            padx=self._scaled(10),
            pady=self._scaled(6),
        )
        label.pack(anchor="w")
        self._bind_row_drag(label)
        return {"root": label}

    def _visible_items(self) -> list[StockItem]:
        return [stock for stock in self.settings.stocks if stock.enabled and stock.has_content()]

    def _visible_boards(self) -> list[BoardItem]:
        return [board for board in self.settings.boards if board.enabled and board.has_content()]

    def _visible_market_keys(self) -> list[str]:
        keys: list[str] = []
        if self.settings.show_gold:
            keys.append("gold")
        if self.settings.show_brent:
            keys.append("brent")
        if self.settings.show_nasdaq:
            keys.append("nasdaq")
        if self.settings.show_shanghai:
            keys.append("shanghai")
        return keys

    def _display_name(self, index: int, stock: StockItem, quote: StockQuote | None) -> str:
        if stock.name_hidden:
            return f"{index}"
        return stock.name or (quote.name if quote else "") or stock.code or f"{index}"

    def _display_board_name(self, index: int, board: BoardItem, quote: BoardQuote | None) -> str:
        if board.name_hidden:
            return f"{index}"
        return board.name or (quote.name if quote else "") or board.code or f"{index}"

    def _percent_display(self, quote: StockQuote | BoardQuote | MarketIndicator | None) -> tuple[str, str]:
        if not quote or quote.percent is None:
            return "--", "#bdbdbd"
        if quote.percent > 0:
            return f"+{quote.percent:.2f}%", "#d44d4d"
        if quote.percent < 0:
            return f"{quote.percent:.2f}%", "#29985b"
        return "0.00%", "#9fa5a9"

    def _price_display(self, quote: StockQuote | BoardQuote | None, item: StockItem | BoardItem) -> str:
        if item.price_hidden:
            return ""
        if not quote or quote.price is None:
            return "--"
        if quote.price >= 1000:
            return f"{quote.price:.0f}"
        if quote.price >= 100:
            return f"{quote.price:.1f}"
        return f"{quote.price:.2f}"

    def _market_display_name(self, key: str, quote: MarketIndicator | None) -> str:
        if quote:
            return quote.name
        return MARKET_INDICATORS.get(key, (key, "", ""))[0]

    def _market_price_display(self, quote: MarketIndicator | None) -> str:
        if not quote or quote.price is None:
            return "--"
        if quote.price >= 1000:
            return f"{quote.price:.0f}"
        if quote.price >= 100:
            return f"{quote.price:.1f}"
        return f"{quote.price:.2f}"

    def toggle_name(self, item: StockItem | BoardItem) -> None:
        item.name_hidden = not item.name_hidden
        save_settings(self.settings)
        self.render_rows({})
        self.refresh_quotes()

    def refresh_quotes(self) -> None:
        self.refresh_job = None
        stocks = [(stock.code, stock.name) for stock in self._visible_items()]
        boards = [(board.code, board.name) for board in self._visible_boards()]
        market_keys = self._visible_market_keys()
        if not stocks and not boards and not market_keys:
            self.schedule_next_refresh()
            return
        if self.refresh_inflight:
            self.schedule_next_refresh()
            return

        self.refresh_inflight = True
        threading.Thread(target=self._fetch_quotes_worker, args=(stocks, boards, market_keys), daemon=True).start()
        self.root.after(100, self.consume_quotes)
        self.schedule_next_refresh()

    def _fetch_quotes_worker(
        self,
        stocks: list[tuple[str, str]],
        boards: list[tuple[str, str]],
        market_keys: list[str],
    ) -> None:
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                stock_future = executor.submit(fetch_stock_quotes, stocks)
                board_future = executor.submit(fetch_board_quotes, boards)
                market_future = executor.submit(fetch_market_indicators, market_keys)
                self.quote_queue.put(
                    (
                        self._future_result(stock_future),
                        self._future_result(board_future),
                        self._future_result(market_future),
                    )
                )
        except Exception:
            self.quote_queue.put(({}, {}, {}))

    def _future_result(self, future):
        try:
            return future.result()
        except Exception:
            return {}

    def consume_quotes(self) -> None:
        try:
            quotes, board_quotes, market_quotes = self.quote_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self.consume_quotes)
            return
        self.refresh_inflight = False
        quotes = merge_quotes_with_cache(quotes, self.last_stock_quotes)
        board_quotes = merge_quotes_with_cache(board_quotes, self.last_board_quotes)
        market_quotes = merge_quotes_with_cache(market_quotes, self.last_market_quotes)
        self.last_stock_quotes = quotes
        self.last_board_quotes = board_quotes
        self.last_market_quotes = market_quotes
        self._cache_resolved_codes(quotes, board_quotes)
        self.render_all_rows(quotes, board_quotes, market_quotes)

    def _cache_resolved_codes(self, quotes: dict[str, StockQuote], board_quotes: dict[str, BoardQuote]) -> None:
        changed = False
        for stock in self.settings.stocks:
            if stock.code:
                continue
            quote = quotes.get(self._stock_key(stock))
            if quote and quote.code:
                stock.code = normalize_stock_code(quote.code)
                changed = True
        for board in self.settings.boards:
            if board.code:
                continue
            quote = board_quotes.get(self._board_key(board))
            if quote and quote.code:
                board.code = normalize_board_code(quote.code)
                changed = True
        if changed:
            save_settings(self.settings)

    def _board_key(self, board: BoardItem) -> str:
        return normalize_board_code(board.code) or board.name.strip()

    def _stock_key(self, stock: StockItem) -> str:
        return normalize_stock_code(stock.code) or stock.name.strip()

    def schedule_next_refresh(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(self._effective_refresh_seconds() * 1000, self.refresh_quotes)

    def _effective_refresh_seconds(self) -> int:
        base = max(1, self.settings.refresh_seconds)
        if self._is_active_market_time():
            return base
        return max(30, base)

    def _is_active_market_time(self) -> bool:
        now = datetime.now()
        current = now.time()
        if now.weekday() < 5 and (time(9, 25) <= current <= time(11, 35) or time(12, 55) <= current <= time(15, 5)):
            return True
        if current >= time(21, 25) or current <= time(5, 5):
            return True
        return False

    def start_right_drag(self, event: tk.Event) -> None:
        self.drag_moved = False
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def on_right_drag(self, event: tk.Event) -> None:
        if not self.drag_start:
            return
        start_x, start_y, window_x, window_y = self.drag_start
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        if abs(dx) + abs(dy) > 3:
            self.drag_moved = True
        self._move_root(window_x + dx, window_y + dy)

    def finish_right_drag(self, event: tk.Event) -> None:
        if self.drag_moved:
            old_x = self.root.winfo_x()
            old_y = self.root.winfo_y()
            self._ensure_root_visible()
            self.chart_popup.follow_root_move(self.root.winfo_x() - old_x, self.root.winfo_y() - old_y)
            self.chart_popup.snap_to_root()
            self.settings.window_x = self.root.winfo_x()
            self.settings.window_y = self.root.winfo_y()
            save_settings(self.settings)
        else:
            self.menu.tk_popup(event.x_root, event.y_root)
        self.drag_start = None

    def start_main_resize(self, event: tk.Event) -> str | None:
        corner = self._main_resize_corner_at(event)
        if not corner:
            return None
        self.root.update_idletasks()
        self.scale_drag_start = (
            event.x_root,
            event.y_root,
            self.settings.scale,
            max(1, self.root.winfo_width()),
            max(1, self.root.winfo_height()),
            self.root.winfo_x(),
            self.root.winfo_y(),
            corner,
        )
        return "break"

    def on_main_resize(self, event: tk.Event) -> str | None:
        if not self.scale_drag_start:
            return None
        start_x, start_y, start_scale, start_w, start_h, window_x, window_y, corner = self.scale_drag_start
        new_scale = scale_from_corner_drag(
            start_scale,
            start_w,
            start_h,
            event.x_root - start_x,
            event.y_root - start_y,
            corner,
        )
        if new_scale != self.settings.scale:
            self.settings.scale = new_scale
            self._apply_scale()
            self.row_layout_signature = None
            self.render_rows({})
            self.root.update_idletasks()
        new_x = window_x
        new_y = window_y
        if "w" in corner:
            new_x = window_x + start_w - max(1, self.root.winfo_width())
        if "n" in corner:
            new_y = window_y + start_h - max(1, self.root.winfo_height())
        self._move_root(new_x, new_y)
        return "break"

    def finish_main_resize(self, _event: tk.Event) -> str | None:
        if not self.scale_drag_start:
            return None
        self.scale_drag_start = None
        self._ensure_root_visible()
        self.settings.window_x = self.root.winfo_x()
        self.settings.window_y = self.root.winfo_y()
        save_settings(self.settings)
        return "break"

    def update_main_resize_cursor(self, event: tk.Event) -> None:
        widget = event.widget
        if not isinstance(widget, tk.Widget):
            return
        if self._main_resize_corner_at(event):
            widget.configure(cursor="sizing")
            return
        if str(widget.cget("cursor")) == "sizing":
            widget.configure(cursor=getattr(widget, "_stock_default_cursor", ""))

    def clear_main_resize_cursor(self, event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, tk.Widget) and str(widget.cget("cursor")) == "sizing":
            widget.configure(cursor=getattr(widget, "_stock_default_cursor", ""))

    def _main_resize_corner_at(self, event: tk.Event) -> str:
        self.root.update_idletasks()
        x = event.x_root - self.root.winfo_x()
        y = event.y_root - self.root.winfo_y()
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        size = max(MAIN_RESIZE_HANDLE, self._scaled(10))
        if x <= size and y <= size:
            return "nw"
        if x >= width - size and y <= size:
            return "ne"
        if x <= size and y >= height - size:
            return "sw"
        if x >= width - size and y >= height - size:
            return "se"
        return ""

    def _move_root(self, x: int, y: int) -> None:
        old_x = self.root.winfo_x()
        old_y = self.root.winfo_y()
        self.root.geometry(f"+{x}+{y}")
        self.root.update_idletasks()
        self.chart_popup.follow_root_move(self.root.winfo_x() - old_x, self.root.winfo_y() - old_y)

    def start_middle_opacity(self, event: tk.Event) -> None:
        self.opacity_drag_start = (event.y_root, self.settings.opacity)

    def on_middle_opacity(self, event: tk.Event) -> None:
        if not self.opacity_drag_start:
            return
        start_y, start_opacity = self.opacity_drag_start
        self.settings.opacity = opacity_from_middle_drag(start_opacity, event.y_root - start_y)
        self.root.attributes("-alpha", self.settings.opacity)

    def finish_middle_opacity(self, _event: tk.Event) -> None:
        if not self.opacity_drag_start:
            return
        self.opacity_drag_start = None
        save_settings(self.settings)

    def reset_window_position(self) -> None:
        self.settings.window_x = 80
        self.settings.window_y = 80
        self.root.geometry(f"+{self.settings.window_x}+{self.settings.window_y}")
        self._ensure_root_visible(save=True)

    def open_opacity(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title("透明度")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        self._apply_window_icon(dialog)
        value = tk.DoubleVar(value=self.settings.opacity)

        ttk.Label(dialog, text="透明度").pack(padx=14, pady=(12, 4))
        scale = ttk.Scale(dialog, from_=0.35, to=1.0, orient="horizontal", variable=value)
        scale.pack(padx=14, pady=6, fill="x")

        def apply_opacity() -> None:
            self.settings.opacity = round(value.get(), 2)
            self.root.attributes("-alpha", self.settings.opacity)
            save_settings(self.settings)

        ttk.Button(dialog, text="应用", command=apply_opacity).pack(padx=14, pady=(6, 12))
        self._place_dialog_near_root(dialog)

    def open_scale(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title("缩放")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        self._apply_window_icon(dialog)
        value = tk.DoubleVar(value=self.settings.scale)

        ttk.Label(dialog, text="小窗大小").pack(padx=14, pady=(12, 4))
        scale = ttk.Scale(dialog, from_=MIN_MAIN_SCALE, to=MAX_MAIN_SCALE, orient="horizontal", variable=value)
        scale.pack(padx=14, pady=6, fill="x")

        def apply_scale() -> None:
            self.settings.scale = round(value.get(), 2)
            save_settings(self.settings)
            self._apply_scale()
            self.row_layout_signature = None
            self.render_rows({})
            self.refresh_quotes()

        ttk.Button(dialog, text="应用", command=apply_scale).pack(padx=14, pady=(6, 12))
        self._place_dialog_near_root(dialog)

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        prepare_dialog_for_hidden_layout(dialog, 960, 720)
        dialog.title("设置")
        dialog.attributes("-topmost", True)
        dialog.minsize(900, 640)
        dialog.resizable(True, True)
        dialog.configure(bg="#0f172a")
        self._apply_window_icon(dialog)

        stock_entries: list[dict[str, object]] = []
        board_entries: list[dict[str, object]] = []
        gold_var = tk.BooleanVar(value=self.settings.show_gold)
        brent_var = tk.BooleanVar(value=self.settings.show_brent)
        nasdaq_var = tk.BooleanVar(value=self.settings.show_nasdaq)
        shanghai_var = tk.BooleanVar(value=self.settings.show_shanghai)
        base_width = 960
        min_height = 720
        settings_ready = False

        content = tk.Frame(dialog, bg="#0f172a", padx=18, pady=14)
        content.pack(fill="both", expand=True)

        def adjust_dialog_size(place: bool = False) -> None:
            dialog.update_idletasks()
            bounds = virtual_screen_bounds(self.root)
            max_width = max(900, bounds.width - 80)
            max_height = max(520, bounds.height - 80)
            wanted_width = max(base_width, content.winfo_reqwidth() + 36)
            wanted_height = max(min_height, content.winfo_reqheight() + 24)
            width = min(wanted_width, max_width)
            height = min(wanted_height, max_height)
            if place:
                dialog.geometry(f"{width}x{height}")
                self._place_dialog_near_root(dialog)
                return
            x, y = clamp_window_position(dialog.winfo_x(), dialog.winfo_y(), width, height, bounds)
            dialog.geometry(f"{width}x{height}+{x}+{y}")

        def relabel_rows(entries: list[dict[str, object]]) -> None:
            index = 1
            for entry_data in entries:
                if entry_data.get("removed"):
                    continue
                index_label = entry_data.get("index_label")
                if index_label:
                    index_label.configure(text=f"{index}. 代码")
                index += 1

        def schedule_settings_resize() -> None:
            if settings_ready:
                dialog.after_idle(lambda: adjust_dialog_size(place=False))

        def label(parent, text: str, size: int = 10, weight: str = "normal") -> tk.Label:
            return tk.Label(parent, text=text, bg="#0f172a", fg="#dbeafe", font=("Microsoft YaHei UI", size, weight))

        def text_entry(parent, variable: tk.StringVar, width: int) -> tk.Entry:
            return tk.Entry(
                parent,
                textvariable=variable,
                width=width,
                bg="#111827",
                fg="#e5e7eb",
                insertbackground="#38bdf8",
                relief="flat",
                highlightthickness=1,
                highlightbackground="#334155",
                highlightcolor="#38bdf8",
                font=("Microsoft YaHei UI", 10),
            )

        def check(parent, text: str, variable: tk.BooleanVar) -> tk.Checkbutton:
            return tk.Checkbutton(
                parent,
                text=text,
                variable=variable,
                bg="#0f172a",
                fg="#cbd5e1",
                activebackground="#0f172a",
                activeforeground="#ffffff",
                selectcolor="#111827",
                font=("Microsoft YaHei UI", 9),
            )

        def button(parent, text: str, command, width: int | None = None) -> tk.Button:
            return tk.Button(
                parent,
                text=text,
                command=command,
                width=width or 0,
                bg="#1e293b",
                fg="#e5e7eb",
                activebackground="#334155",
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=10,
                pady=5,
                font=("Microsoft YaHei UI", 9),
            )

        title_row = tk.Frame(content, bg="#0f172a")
        title_row.pack(fill="x", pady=(0, 6))
        label(title_row, "标的", 11, "bold").pack(side="left")
        tools_row = tk.Frame(title_row, bg="#0f172a")
        tools_row.pack(side="right")
        stock_rows = tk.Frame(content, bg="#0f172a")
        stock_rows.pack(fill="x")

        def add_stock_row(stock: StockItem | None = None) -> None:
            item = stock or StockItem()
            index = len(stock_entries) + 1
            row = tk.Frame(stock_rows, bg="#0f172a")
            row.pack(fill="x", pady=(4, 0))

            index_label = label(row, f"{index}. 代码")
            index_label.grid(row=0, column=0, sticky="w", padx=(0, 4))
            code_var = tk.StringVar(value=item.code)
            text_entry(row, code_var, 13).grid(row=0, column=1, padx=(0, 10))

            label(row, "名称").grid(row=0, column=2, sticky="w", padx=(0, 4))
            name_var = tk.StringVar(value=item.name)
            text_entry(row, name_var, 18).grid(row=0, column=3, padx=(0, 10))

            enabled_var = tk.BooleanVar(value=item.enabled)
            hidden_var = tk.BooleanVar(value=item.name_hidden)
            price_hidden_var = tk.BooleanVar(value=item.price_hidden)
            check(row, "显示", enabled_var).grid(row=0, column=4, padx=(0, 8), sticky="w")
            check(row, "隐藏名称", hidden_var).grid(row=0, column=5, padx=(0, 8), sticky="w")
            check(row, "隐藏价格", price_hidden_var).grid(row=0, column=6, padx=(0, 8), sticky="w")
            entry = {
                "row": row,
                "index_label": index_label,
                "code": code_var,
                "name": name_var,
                "enabled": enabled_var,
                "hidden": hidden_var,
                "price_hidden": price_hidden_var,
                "removed": False,
            }

            def remove_row() -> None:
                entry["removed"] = True
                row.destroy()
                relabel_rows(stock_entries)
                schedule_settings_resize()

            button(row, "删除", remove_row, width=5).grid(row=0, column=7, sticky="e")
            stock_entries.append(entry)
            relabel_rows(stock_entries)
            schedule_settings_resize()

        for stock in self._settings_items(self.settings.stocks, StockItem):
            add_stock_row(stock)

        button(content, "添加标的", add_stock_row).pack(anchor="w", pady=(8, 14))

        label(content, "板块", 11, "bold").pack(anchor="w", pady=(0, 6))
        board_rows = tk.Frame(content, bg="#0f172a")
        board_rows.pack(fill="x")

        def add_board_row(board: BoardItem | None = None) -> None:
            item = board or BoardItem()
            index = len(board_entries) + 1
            row = tk.Frame(board_rows, bg="#0f172a")
            row.pack(fill="x", pady=(4, 0))

            index_label = label(row, f"{index}. 代码")
            index_label.grid(row=0, column=0, sticky="w", padx=(0, 4))
            code_var = tk.StringVar(value=item.code)
            text_entry(row, code_var, 13).grid(row=0, column=1, padx=(0, 10))

            label(row, "名称").grid(row=0, column=2, sticky="w", padx=(0, 4))
            name_var = tk.StringVar(value=item.name)
            text_entry(row, name_var, 18).grid(row=0, column=3, padx=(0, 10))

            enabled_var = tk.BooleanVar(value=item.enabled)
            hidden_var = tk.BooleanVar(value=item.name_hidden)
            price_hidden_var = tk.BooleanVar(value=item.price_hidden)
            check(row, "显示", enabled_var).grid(row=0, column=4, padx=(0, 8), sticky="w")
            check(row, "隐藏名称", hidden_var).grid(row=0, column=5, padx=(0, 8), sticky="w")
            check(row, "隐藏价格", price_hidden_var).grid(row=0, column=6, padx=(0, 8), sticky="w")
            entry = {
                "row": row,
                "index_label": index_label,
                "code": code_var,
                "name": name_var,
                "enabled": enabled_var,
                "hidden": hidden_var,
                "price_hidden": price_hidden_var,
                "removed": False,
            }

            def remove_row() -> None:
                entry["removed"] = True
                row.destroy()
                relabel_rows(board_entries)
                schedule_settings_resize()

            button(row, "删除", remove_row, width=5).grid(row=0, column=7, sticky="e")
            board_entries.append(entry)
            relabel_rows(board_entries)
            schedule_settings_resize()

        for board in self._settings_items(self.settings.boards, BoardItem):
            add_board_row(board)

        button(content, "添加板块", add_board_row).pack(anchor="w", pady=(8, 14))

        market_row = tk.Frame(content, bg="#0f172a")
        market_row.pack(fill="x", pady=(0, 12))
        label(market_row, "市场", 11, "bold").grid(row=0, column=0, sticky="w", padx=(0, 10))
        check(market_row, "黄金", gold_var).grid(row=0, column=1, padx=(0, 8))
        check(market_row, "布油", brent_var).grid(row=0, column=2, padx=(0, 8))
        check(market_row, "纳指", nasdaq_var).grid(row=0, column=3, padx=(0, 8))
        check(market_row, "上证", shanghai_var).grid(row=0, column=4, padx=(0, 8))

        refresh_row = tk.Frame(content, bg="#0f172a")
        refresh_row.pack(fill="x", pady=(0, 10))
        label(refresh_row, "刷新间隔(秒)").pack(side="left")
        refresh_var = tk.StringVar(value=str(self.settings.refresh_seconds))
        text_entry(refresh_row, refresh_var, 8).pack(side="left", padx=8)

        hint = "标的可填股票或ETF代码/名称；板块填代码或名称也可刷新。"
        tk.Label(content, text=hint, bg="#0f172a", fg="#94a3b8", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 12))

        button_row = tk.Frame(content, bg="#0f172a")
        button_row.pack(fill="x")

        def collect_dialog_settings() -> AppSettings | None:
            stocks: list[StockItem] = []
            for entry in stock_entries:
                if entry.get("removed"):
                    continue
                code_var = entry["code"]
                name_var = entry["name"]
                enabled_var = entry["enabled"]
                hidden_var = entry["hidden"]
                price_hidden_var = entry["price_hidden"]
                code = normalize_stock_code(code_var.get())
                item = StockItem(
                    code=code,
                    name=name_var.get().strip(),
                    enabled=enabled_var.get(),
                    name_hidden=hidden_var.get(),
                    price_hidden=price_hidden_var.get(),
                )
                if item.has_content():
                    stocks.append(item)
            boards: list[BoardItem] = []
            for entry in board_entries:
                if entry.get("removed"):
                    continue
                code_var = entry["code"]
                name_var = entry["name"]
                enabled_var = entry["enabled"]
                hidden_var = entry["hidden"]
                price_hidden_var = entry["price_hidden"]
                code = normalize_board_code(code_var.get())
                item = BoardItem(
                    code=code,
                    name=name_var.get().strip(),
                    enabled=enabled_var.get(),
                    name_hidden=hidden_var.get(),
                    price_hidden=price_hidden_var.get(),
                )
                if item.has_content():
                    boards.append(item)
            try:
                refresh_seconds = max(1, int(refresh_var.get()))
            except ValueError:
                messagebox.showerror("设置错误", "刷新间隔需要填写数字，且不少于 1 秒。")
                return None

            new_settings = AppSettings(
                stocks=stocks,
                boards=boards,
                show_gold=gold_var.get(),
                show_brent=brent_var.get(),
                show_nasdaq=nasdaq_var.get(),
                show_shanghai=shanghai_var.get(),
                opacity=self.settings.opacity,
                scale=self.settings.scale,
                refresh_seconds=refresh_seconds,
                window_x=self.settings.window_x,
                window_y=self.settings.window_y,
                chart_width=self.settings.chart_width,
                chart_height=self.settings.chart_height,
            )
            return new_settings

        def apply_settings(new_settings: AppSettings) -> None:
            self.settings = new_settings
            self.chart_popup.settings = self.settings
            save_settings(self.settings)
            self.row_layout_signature = None
            self.render_rows({})
            self.refresh_quotes()

        def export_current_settings() -> None:
            new_settings = collect_dialog_settings()
            if not new_settings:
                return
            path = filedialog.asksaveasfilename(
                parent=dialog,
                title="导出配置",
                defaultextension=".json",
                filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")],
                initialfile="A股盯盘小窗配置.json",
            )
            if not path:
                return
            try:
                export_settings(new_settings, path)
            except OSError as exc:
                messagebox.showerror("导出失败", f"配置文件写入失败：{exc}")
                return
            messagebox.showinfo("导出完成", "配置已导出。")

        def import_current_settings() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="导入配置",
                filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")],
            )
            if not path:
                return
            try:
                imported_settings = import_settings(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("导入失败", f"配置文件读取失败：{exc}")
                return
            apply_settings(imported_settings)
            dialog.destroy()
            messagebox.showinfo("导入完成", "配置已导入并刷新。")

        def save_dialog() -> None:
            new_settings = collect_dialog_settings()
            if not new_settings:
                return
            apply_settings(new_settings)
            dialog.destroy()

        button(button_row, "保存", save_dialog, width=8).pack(side="right")
        button(button_row, "取消", dialog.destroy, width=8).pack(side="right", padx=8)
        button(tools_row, "导入配置", import_current_settings, width=9).pack(side="right", padx=(8, 0))
        button(tools_row, "导出配置", export_current_settings, width=9).pack(side="right")
        settings_ready = True
        adjust_dialog_size(place=True)

    def close(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.chart_popup.close()
        self._ensure_root_visible()
        self.settings.window_x = self.root.winfo_x()
        self.settings.window_y = self.root.winfo_y()
        save_settings(self.settings)
        self.root.destroy()

    def _font(self, size: int, family: str = "Microsoft YaHei UI", weight: str = "normal") -> tuple[str, int, str]:
        return (family, max(7, self._scaled(size)), weight)

    def _scaled(self, value: int) -> int:
        return max(1, round(value * self.settings.scale))

    def _apply_scale(self) -> None:
        self.container.configure(padx=self._scaled(6), pady=self._scaled(4))

    def _settings_items(self, items: list[StockItem] | list[BoardItem], item_type):
        filled_items = list(items)
        empty_count = max(0, 3 - len(filled_items))
        return filled_items + [item_type() for _ in range(empty_count)]

    def _resource_path(self, relative_path: str) -> Path:
        base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        return base_path / relative_path

    def _apply_window_icon(self, window: tk.Tk | tk.Toplevel) -> None:
        if self.icon_path.exists():
            try:
                window.iconbitmap(str(self.icon_path))
            except tk.TclError:
                pass

    def _place_dialog_near_root(self, dialog: tk.Toplevel) -> None:
        self._ensure_root_visible(save=True)
        self.root.update_idletasks()
        dialog.update_idletasks()

        gap = 8
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = max(1, self.root.winfo_width())
        dialog_w = max(1, dialog.winfo_width())
        dialog_h = max(1, dialog.winfo_height())

        bounds = virtual_screen_bounds(self.root)
        x = root_x + root_w + gap
        if x + dialog_w > bounds.right - 20:
            x = root_x - dialog_w - gap

        y = root_y
        x, y = clamp_window_position(x, y, dialog_w, dialog_h, bounds)

        dialog.geometry(f"+{x}+{y}")
        dialog.deiconify()
        dialog.update_idletasks()
        reveal_prepared_dialog(dialog)
        dialog.lift()
        dialog.focus_force()

    def _ensure_root_visible(self, save: bool = False) -> None:
        self.root.update_idletasks()
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        bounds = virtual_screen_bounds(self.root)
        x, y = clamp_window_position(self.root.winfo_x(), self.root.winfo_y(), width, height, bounds)
        if x != self.root.winfo_x() or y != self.root.winfo_y():
            self.root.geometry(f"+{x}+{y}")
            self.root.update_idletasks()
        if save:
            self.settings.window_x = x
            self.settings.window_y = y
            save_settings(self.settings)
