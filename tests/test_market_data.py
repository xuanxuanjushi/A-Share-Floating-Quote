import pathlib
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.market_data import MarketIndicator, fetch_market_indicators


class MarketDataSpeedTests(unittest.TestCase):
    def test_market_indicator_groups_fetch_in_parallel(self) -> None:
        def slow_sina(_symbols, _timeout):
            time.sleep(0.05)
            return {"gold": MarketIndicator("gold", "黄金", 1.0, 4000)}

        def slow_commodity(_codes, _timeout):
            time.sleep(0.05)
            return {"brent": MarketIndicator("brent", "布油", 2.0, 80)}

        with patch("stock_floater.market_data._fetch_market_sina_indicators", side_effect=slow_sina):
            with patch("stock_floater.market_data._fetch_market_commodity_indicators", side_effect=slow_commodity):
                start = time.perf_counter()
                result = fetch_market_indicators(["gold", "brent"], timeout=1)
                elapsed = time.perf_counter() - start

        self.assertIn("gold", result)
        self.assertIn("brent", result)
        self.assertLess(elapsed, 0.09)


if __name__ == "__main__":
    unittest.main()
