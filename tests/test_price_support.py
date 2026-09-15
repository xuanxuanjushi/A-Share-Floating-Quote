import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import stock_floater.config as config
from stock_floater.market_data import StockQuote


class PriceSupportTests(unittest.TestCase):
    def test_quote_carries_current_price(self) -> None:
        quote = StockQuote("sh600519", "贵州茅台", 1.23, 1520.5)
        self.assertEqual(quote.price, 1520.5)

    def test_settings_preserve_price_visibility(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(
                stocks=[config.StockItem(code="510300", price_hidden=False)],
                boards=[config.BoardItem(name="银行", price_hidden=False)],
            )
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertFalse(loaded.stocks[0].price_hidden)
        self.assertFalse(loaded.boards[0].price_hidden)

    def test_settings_preserve_item_display_enabled(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(
                stocks=[config.StockItem(code="510300", enabled=False)],
                boards=[config.BoardItem(name="银行", enabled=False)],
            )
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertFalse(loaded.stocks[0].enabled)
        self.assertFalse(loaded.boards[0].enabled)

    def test_old_settings_default_items_to_enabled(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                '{"stocks":[{"code":"510300"}],"boards":[{"name":"银行"}]}',
                encoding="utf-8",
            )
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertTrue(loaded.stocks[0].enabled)
        self.assertTrue(loaded.boards[0].enabled)

    def test_exported_settings_can_be_imported_on_another_computer(self) -> None:
        export_path = pathlib.Path(tempfile.mkdtemp()) / "exported-settings.json"
        settings = config.AppSettings(
            stocks=[config.StockItem(code="510300", name="沪深300ETF", enabled=False, name_hidden=True, price_hidden=True)],
            boards=[config.BoardItem(code="BK0800", name="人工智能", enabled=True, price_hidden=True)],
            show_gold=False,
            show_brent=True,
            show_nasdaq=False,
            show_shanghai=True,
            opacity=0.66,
            scale=1.12,
            refresh_seconds=5,
            chart_width=520,
            chart_height=318,
        )

        config.export_settings(settings, export_path)
        imported = config.import_settings(export_path)

        self.assertEqual(imported.stocks[0].code, "sh510300")
        self.assertFalse(imported.stocks[0].enabled)
        self.assertTrue(imported.stocks[0].name_hidden)
        self.assertTrue(imported.stocks[0].price_hidden)
        self.assertEqual(imported.boards[0].code, "BK0800")
        self.assertTrue(imported.boards[0].enabled)
        self.assertFalse(imported.show_gold)
        self.assertFalse(imported.show_nasdaq)
        self.assertEqual(imported.refresh_seconds, 5)
        self.assertEqual(imported.chart_width, 520)
        self.assertEqual(imported.chart_height, 318)

    def test_settings_preserve_more_than_three_items(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(
                stocks=[config.StockItem(code=f"60000{i}") for i in range(5)],
                boards=[config.BoardItem(name=f"板块{i}") for i in range(4)],
            )
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertEqual(len(loaded.stocks), 5)
        self.assertEqual(len(loaded.boards), 4)

    def test_settings_preserve_chart_popup_size(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(chart_width=520, chart_height=318)
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertEqual(loaded.chart_width, 520)
        self.assertEqual(loaded.chart_height, 318)


if __name__ == "__main__":
    unittest.main()
