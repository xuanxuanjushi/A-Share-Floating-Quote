import unittest

from stock_floater.config import normalize_stock_code
from stock_floater.market_data import _eastmoney_stock_secid


class EtfSupportTests(unittest.TestCase):
    def test_normalizes_shanghai_etf_codes(self) -> None:
        self.assertEqual(normalize_stock_code("510300"), "sh510300")
        self.assertEqual(normalize_stock_code("588000"), "sh588000")
        self.assertEqual(normalize_stock_code("sz512010"), "sh512010")
        self.assertEqual(_eastmoney_stock_secid("510300"), "1.510300")

    def test_normalizes_shenzhen_etf_codes(self) -> None:
        self.assertEqual(normalize_stock_code("159915"), "sz159915")
        self.assertEqual(normalize_stock_code("sh159915"), "sz159915")
        self.assertEqual(_eastmoney_stock_secid("159915"), "0.159915")


if __name__ == "__main__":
    unittest.main()
