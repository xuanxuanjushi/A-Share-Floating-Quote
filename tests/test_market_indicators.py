import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import stock_floater.config as config
from stock_floater.market_data import MarketIndicator, _parse_eastmoney_quote_data, _parse_eastmoney_ulist_data, _parse_sina_market_data


class MarketIndicatorTests(unittest.TestCase):
    def test_settings_preserve_market_indicator_switches(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(show_gold=False, show_nasdaq=True, show_shanghai=False, show_brent=True)
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertFalse(loaded.show_gold)
        self.assertTrue(loaded.show_nasdaq)
        self.assertFalse(loaded.show_shanghai)
        self.assertTrue(loaded.show_brent)

    def test_parse_eastmoney_quote_data_reads_price_and_percent(self) -> None:
        quote = _parse_eastmoney_quote_data(
            "gold",
            {
                "f58": "黄金/美元",
                "f43": 417570,
                "f170": 125,
            },
        )

        self.assertEqual(quote.name, "黄金/美元")
        self.assertEqual(quote.price, 4175.7)
        self.assertEqual(quote.percent, 1.25)
        self.assertIsInstance(quote, MarketIndicator)

    def test_parse_sina_market_data_reads_builtin_indicators(self) -> None:
        gold = _parse_sina_market_data("gold", "hf_XAU", ["4174.77", "4123.61"])
        nasdaq = _parse_sina_market_data("nasdaq", "gb_ixic", ["纳斯达克", "25832.6716", "-0.80"])
        shanghai = _parse_sina_market_data("shanghai", "s_sh000001", ["上证指数", "4043.6432", "14.7394", "0.37"])

        self.assertEqual(gold.price, 4174.77)
        self.assertAlmostEqual(gold.percent or 0, 1.24, places=1)
        self.assertEqual(nasdaq.price, 25832.6716)
        self.assertEqual(nasdaq.percent, -0.80)
        self.assertEqual(shanghai.price, 4043.6432)
        self.assertEqual(shanghai.percent, 0.37)

    def test_parse_eastmoney_ulist_data_reads_brent_contract_percent(self) -> None:
        brent = _parse_eastmoney_ulist_data(
            "brent",
            {
                "f2": 78.41,
                "f3": 5.73,
                "f12": "B00Y",
                "f14": "布伦特原油当月连续",
            },
        )

        self.assertEqual(brent.name, "布伦特原油")
        self.assertEqual(brent.price, 78.41)
        self.assertEqual(brent.percent, 5.73)


if __name__ == "__main__":
    unittest.main()
