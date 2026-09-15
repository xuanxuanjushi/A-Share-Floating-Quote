import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.config import AppSettings, BoardItem, StockItem
from stock_floater.market_data import StockQuote
from stock_floater.ui import StockFloaterApp, opacity_from_middle_drag, scale_from_corner_drag
from stock_floater.ui import prepare_dialog_for_hidden_layout, reveal_prepared_dialog


def _first_render_widget(app: StockFloaterApp):
    rows = getattr(app, "row_widgets", None)
    if rows:
        return rows[0]["root"]
    return app.row_frames[0]


def _first_percent_text(app: StockFloaterApp) -> str:
    rows = getattr(app, "row_widgets", None)
    if rows:
        return rows[0]["percent_label"].cget("text")
    return app.row_frames[0].grid_slaves(row=0)[0].cget("text")


class RenderStabilityTests(unittest.TestCase):
    def test_middle_drag_adjusts_opacity_with_bounds(self) -> None:
        self.assertEqual(opacity_from_middle_drag(0.80, -60), 0.92)
        self.assertEqual(opacity_from_middle_drag(0.80, 60), 0.68)
        self.assertEqual(opacity_from_middle_drag(0.98, -60), 1.0)
        self.assertEqual(opacity_from_middle_drag(0.38, 60), 0.35)

    def test_corner_drag_adjusts_main_window_scale_with_bounds(self) -> None:
        self.assertEqual(scale_from_corner_drag(0.82, 260, 700, 120, 60, "se"), 1.2)
        self.assertEqual(scale_from_corner_drag(0.82, 260, 700, -80, -30, "se"), 0.57)
        self.assertEqual(scale_from_corner_drag(0.82, 260, 700, 80, 0, "nw"), 0.57)
        self.assertEqual(scale_from_corner_drag(1.8, 260, 700, 500, 0, "se"), 1.8)

    def test_updates_quote_text_without_rebuilding_row_widget(self) -> None:
        app = StockFloaterApp()
        app.root.withdraw()
        try:
            app.settings = AppSettings(stocks=[StockItem(code="sh600519", name="贵州茅台")])
            app.render_all_rows(
                {"sh600519": StockQuote("sh600519", "贵州茅台", 1.23, 1520.5)},
                {},
                {},
            )
            first_widget = _first_render_widget(app)

            app.render_all_rows(
                {"sh600519": StockQuote("sh600519", "贵州茅台", 2.34, 1522.5)},
                {},
                {},
            )

            self.assertIs(_first_render_widget(app), first_widget)
            self.assertEqual(_first_percent_text(app), "+2.34%")
        finally:
            app.root.destroy()

    def test_price_and_percent_labels_have_chart_click_bindings(self) -> None:
        app = StockFloaterApp()
        app.root.withdraw()
        try:
            app.settings = AppSettings(stocks=[StockItem(code="sh600519", name="贵州茅台")])
            app.render_all_rows(
                {"sh600519": StockQuote("sh600519", "贵州茅台", 1.23, 1520.5)},
                {},
                {},
            )
            row = app.row_widgets[0]

            self.assertTrue(row["price_label"].bind("<Button-1>"))
            self.assertTrue(row["percent_label"].bind("<Button-1>"))
        finally:
            app.root.destroy()

    def test_disabled_stock_and_board_are_kept_but_not_rendered(self) -> None:
        app = StockFloaterApp()
        app.root.withdraw()
        try:
            enabled_stock = StockItem(code="sh600519", name="贵州茅台", enabled=True)
            disabled_stock = StockItem(code="sh510300", name="沪深300ETF", enabled=False)
            enabled_board = BoardItem(code="BK0800", name="人工智能", enabled=True)
            disabled_board = BoardItem(code="BK0475", name="证券", enabled=False)
            app.settings = AppSettings(
                stocks=[enabled_stock, disabled_stock],
                boards=[enabled_board, disabled_board],
                show_gold=False,
                show_brent=False,
                show_nasdaq=False,
                show_shanghai=False,
            )

            self.assertEqual(app._visible_items(), [enabled_stock])
            self.assertEqual(app._visible_boards(), [enabled_board])
        finally:
            app.root.destroy()

    def test_settings_dialog_can_be_prepared_hidden_before_final_placement(self) -> None:
        class FakeDialog:
            def __init__(self) -> None:
                self.state_value = "normal"
                self.alpha = 1.0
                self.geometry_value = ""

            def withdraw(self) -> None:
                self.state_value = "withdrawn"

            def state(self) -> str:
                return self.state_value

            def attributes(self, name: str, value: float | None = None) -> float | None:
                if name != "-alpha":
                    return None
                if value is None:
                    return self.alpha
                self.alpha = value
                return None

            def geometry(self, value: str | None = None) -> str:
                if value is not None:
                    self.geometry_value = value
                return self.geometry_value

        dialog = FakeDialog()

        prepare_dialog_for_hidden_layout(dialog, 960, 720)

        self.assertEqual(dialog.state(), "withdrawn")
        self.assertEqual(round(float(dialog.attributes("-alpha")), 2), 0.0)
        self.assertIn("960x720", dialog.geometry())
        self.assertIn("-32000+-32000", dialog.geometry())

        reveal_prepared_dialog(dialog)
        self.assertEqual(round(float(dialog.attributes("-alpha")), 2), 1.0)

    def test_refresh_loop_schedules_next_tick_immediately_and_skips_overlap(self) -> None:
        app = StockFloaterApp()
        app.root.withdraw()
        try:
            app.settings = AppSettings(stocks=[StockItem(code="sh600519", name="贵州茅台")], refresh_seconds=1)
            with patch("stock_floater.ui.threading.Thread") as thread_class:
                app.refresh_quotes()
                self.assertTrue(app.refresh_inflight)
                self.assertIsNotNone(app.refresh_job)
                self.assertEqual(thread_class.call_count, 1)

                app.refresh_quotes()
                self.assertEqual(thread_class.call_count, 1)
        finally:
            if app.refresh_job:
                app.root.after_cancel(app.refresh_job)
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
