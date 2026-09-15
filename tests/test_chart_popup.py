import pathlib
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.chart_data import ChartPoint, ChartSeries, ChartTarget
from stock_floater.chart_popup import (
    chart_pan_offset_from_ctrl_drag,
    chart_popup_position,
    chart_view_count_from_ctrl_wheel,
    intraday_percent_range,
    intraday_point_percent,
    intraday_x_ratio_from_label,
    macd_values,
    moving_average_values,
    move_window_position,
    resize_chart_dimensions,
    visible_daily_points,
)
from stock_floater.chart_popup import ChartPopup, ChartPopupWindow
from stock_floater.windowing import Rect


class ChartPopupTests(unittest.TestCase):
    def test_places_popup_on_left_of_root_when_space_allows(self) -> None:
        bounds = Rect(0, 0, 1920, 1080)

        x, y = chart_popup_position(600, 200, 160, 120, 360, 220, bounds)

        self.assertEqual((x, y), (232, 200))

    def test_places_popup_on_right_when_left_would_leave_screen(self) -> None:
        bounds = Rect(0, 0, 1920, 1080)

        x, y = chart_popup_position(80, 200, 160, 120, 360, 220, bounds)

        self.assertEqual((x, y), (248, 200))

    def test_stacks_multiple_popups_vertically_aligned_to_root_edge(self) -> None:
        bounds = Rect(0, 0, 1920, 1080)

        first_x, first_y = chart_popup_position(600, 200, 160, 500, 360, 220, bounds, stack_index=0)
        second_x, second_y = chart_popup_position(600, 200, 160, 500, 360, 220, bounds, stack_index=1)

        self.assertEqual((first_x, first_y), (232, 200))
        self.assertEqual((second_x, second_y), (232, 428))

    def test_popup_move_position_uses_drag_delta(self) -> None:
        x, y = move_window_position(232, 200, 500, 300, 540, 260)

        self.assertEqual((x, y), (272, 160))

    def test_resizes_chart_proportionally_from_corner_drag(self) -> None:
        width, height, move_x, move_y = resize_chart_dimensions(360, 220, 80, 20, "se")

        self.assertEqual(width, 440)
        self.assertEqual(height, 269)
        self.assertEqual((move_x, move_y), (0, 0))

    def test_resizes_chart_from_top_left_and_moves_origin(self) -> None:
        width, height, move_x, move_y = resize_chart_dimensions(360, 220, -60, -40, "nw")

        self.assertEqual(width, 425)
        self.assertEqual(height, 260)
        self.assertEqual((move_x, move_y), (-65, -40))

    def test_resize_keeps_minimum_size(self) -> None:
        width, height, _move_x, _move_y = resize_chart_dimensions(360, 220, -1000, -1000, "se")

        self.assertEqual(width, 240)
        self.assertEqual(height, 147)

    def test_ctrl_wheel_zoom_changes_visible_daily_count(self) -> None:
        self.assertEqual(chart_view_count_from_ctrl_wheel(44, 120), 37)
        self.assertEqual(chart_view_count_from_ctrl_wheel(44, -120), 51)
        self.assertEqual(chart_view_count_from_ctrl_wheel(20, 120), 20)
        self.assertEqual(chart_view_count_from_ctrl_wheel(118, -120), 120)

    def test_ctrl_right_drag_pans_daily_view(self) -> None:
        self.assertEqual(chart_pan_offset_from_ctrl_drag(0, 44, 12, 30), 4)
        self.assertEqual(chart_pan_offset_from_ctrl_drag(8, -36, 12, 30), 5)
        self.assertEqual(chart_pan_offset_from_ctrl_drag(28, 80, 12, 30), 30)

    def test_visible_daily_points_respects_offset_from_latest(self) -> None:
        points = [ChartPoint(str(index), index, index, index, index) for index in range(10)]

        visible = visible_daily_points(points, 4, 2)

        self.assertEqual([point.label for point in visible], ["4", "5", "6", "7"])

    def test_moving_average_and_macd_values_are_calculated(self) -> None:
        points = [ChartPoint(str(index), index, float(index), index, index) for index in range(1, 13)]

        ma5 = moving_average_values(points, 5)
        macd = macd_values(points)

        self.assertIsNone(ma5[3])
        self.assertEqual(ma5[4], 3.0)
        self.assertEqual(len(macd), len(points))
        self.assertGreater(macd[-1][0], 0)

    def test_intraday_percent_axis_uses_previous_close_and_ten_percent_range(self) -> None:
        points = [
            ChartPoint("09:30", 10.0, 10.0, 10.0, 10.0),
            ChartPoint("10:00", 10.5, 10.5, 10.5, 10.5),
            ChartPoint("15:00", 11.0, 11.0, 11.0, 11.0),
        ]

        self.assertEqual(intraday_point_percent(points[0], 10.0), 0.0)
        self.assertEqual(intraday_point_percent(points[-1], 10.0), 10.0)
        self.assertEqual(intraday_percent_range(points, 10.0), (-10.0, 10.0))

    def test_intraday_x_ratio_respects_a_share_trading_time(self) -> None:
        self.assertEqual(intraday_x_ratio_from_label("09:30"), 0.0)
        self.assertAlmostEqual(intraday_x_ratio_from_label("11:30"), 120 / 240)
        self.assertAlmostEqual(intraday_x_ratio_from_label("13:00"), 120 / 240)
        self.assertEqual(intraday_x_ratio_from_label("15:00"), 1.0)

    def test_resize_corners_are_invisible_so_close_button_is_clear(self) -> None:
        root = tk.Tk()
        root.withdraw()
        popup = ChartPopup(root)
        window = ChartPopupWindow(popup, ChartTarget("测试", "", "暂无"), "intraday")
        try:
            window._create_window()
            window._draw_controls()
            root.update()

            handle_items = window.canvas.find_withtag("resize_handle") if window.canvas else ()
            close_items = window.canvas.find_withtag("close_button") if window.canvas else ()

            self.assertEqual(handle_items, ())
            self.assertGreaterEqual(len(close_items), 2)
        finally:
            window.close()
            root.destroy()

    def test_chart_popup_keeps_last_good_chart_when_refresh_fails(self) -> None:
        root = tk.Tk()
        root.withdraw()
        popup = ChartPopup(root)
        window = ChartPopupWindow(popup, ChartTarget("测试", "", "暂无"), "daily")
        good_series = ChartSeries("测试", [ChartPoint("01", 1, 2, 3, 1), ChartPoint("02", 2, 3, 4, 2)])
        failed_series = ChartSeries("测试", [], "图表暂不可用，稍后自动重试。")
        try:
            window._create_window()
            window._show_series(good_series)
            root.update()

            window._show_series(failed_series)
            root.update()

            self.assertIs(window.current_series, good_series)
        finally:
            window.close()
            root.destroy()

    def test_intraday_chart_uses_only_zero_axis_without_extra_horizontal_grid_lines(self) -> None:
        root = tk.Tk()
        root.withdraw()
        popup = ChartPopup(root)
        window = ChartPopupWindow(popup, ChartTarget("测试", "", "暂无"), "intraday")
        series = ChartSeries(
            "测试",
            [
                ChartPoint("09:30", 10, 10, 10, 10),
                ChartPoint("10:30", 10.2, 10.2, 10.2, 10.2),
                ChartPoint("15:00", 10.5, 10.5, 10.5, 10.5),
            ],
            percent_base=10,
        )
        try:
            window._create_window()
            window._draw_intraday(series)
            root.update()

            line_items = window.canvas.find_all() if window.canvas else ()
            horizontal_lines = []
            for item_id in line_items:
                if window.canvas.type(item_id) != "line":
                    continue
                coords = window.canvas.coords(item_id)
                if len(coords) == 4 and coords[1] == coords[3]:
                    horizontal_lines.append(item_id)

            self.assertEqual(len(horizontal_lines), 1)
        finally:
            window.close()
            root.destroy()

    def test_chart_popup_can_keep_multiple_windows_open_until_manual_close(self) -> None:
        root = tk.Tk()
        root.withdraw()
        popup = ChartPopup(root)
        try:
            popup.show(ChartTarget("测试1", "", "暂无"), "daily")
            popup.show(ChartTarget("测试2", "", "暂无"), "intraday")
            root.update()

            self.assertEqual(len(popup.windows), 2)

            popup.close()
            root.update()
            self.assertEqual(len(popup.windows), 0)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
