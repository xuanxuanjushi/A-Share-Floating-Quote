import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.market_data import MarketIndicator, StockQuote
from stock_floater.quote_cache import merge_quotes_with_cache


class QuoteCacheTests(unittest.TestCase):
    def test_keeps_last_good_quote_when_refresh_returns_empty_values(self) -> None:
        cached = {"sh600519": StockQuote("sh600519", "贵州茅台", 1.2, 1500.0)}
        latest = {"sh600519": StockQuote("sh600519", "贵州茅台", None, None, "暂不可用")}

        merged = merge_quotes_with_cache(latest, cached)

        self.assertEqual(merged["sh600519"].percent, 1.2)
        self.assertEqual(merged["sh600519"].price, 1500.0)

    def test_keeps_last_good_percent_when_refresh_only_has_price(self) -> None:
        cached = {"sh600519": StockQuote("sh600519", "贵州茅台", 1.2, 1500.0)}
        latest = {"sh600519": StockQuote("sh600519", "贵州茅台", None, 1501.0)}

        merged = merge_quotes_with_cache(latest, cached)

        self.assertEqual(merged["sh600519"].percent, 1.2)
        self.assertEqual(merged["sh600519"].price, 1500.0)

    def test_keeps_first_error_when_no_cached_quote_exists(self) -> None:
        latest = {"brent": MarketIndicator("brent", "布伦特原油", None, None, "暂不可用")}

        merged = merge_quotes_with_cache(latest, {})

        self.assertEqual(merged["brent"].error, "暂不可用")
        self.assertIsNone(merged["brent"].percent)


if __name__ == "__main__":
    unittest.main()
