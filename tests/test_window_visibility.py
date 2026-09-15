import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.windowing import Rect, clamp_window_position, snap_window_position


class WindowVisibilityTests(unittest.TestCase):
    def test_keeps_visible_position_unchanged(self) -> None:
        bounds = Rect(0, 0, 1920, 1080)

        self.assertEqual(clamp_window_position(100, 120, 180, 120, bounds), (100, 120))

    def test_clamps_window_saved_outside_right_bottom_edge(self) -> None:
        bounds = Rect(0, 0, 1920, 1080)

        x, y = clamp_window_position(2400, 1600, 180, 120, bounds)

        self.assertLessEqual(x + 180, bounds.right - 20)
        self.assertLessEqual(y + 120, bounds.bottom - 20)

    def test_supports_negative_virtual_screen_coordinates(self) -> None:
        bounds = Rect(-1280, 0, 3200, 1080)

        self.assertEqual(clamp_window_position(-900, 120, 180, 120, bounds), (-900, 120))
        self.assertEqual(clamp_window_position(-1600, 120, 180, 120, bounds), (-1260, 120))

    def test_snaps_window_to_neighbor_with_fixed_gap(self) -> None:
        x, y = snap_window_position(414, 208, 360, 220, [Rect(780, 200, 260, 700)])

        self.assertEqual((x, y), (412, 200))

    def test_leaves_window_when_not_near_neighbor(self) -> None:
        x, y = snap_window_position(300, 100, 360, 220, [Rect(780, 200, 260, 700)])

        self.assertEqual((x, y), (300, 100))


if __name__ == "__main__":
    unittest.main()
