import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.market_data import BoardQuote, _board_page_urls, _parse_board_quote_data, _parse_board_row


class BoardQuoteTests(unittest.TestCase):
    def test_parse_board_row_reads_ai_board_quote(self) -> None:
        quote = _parse_board_row({"f12": "BK0800", "f14": "人工智能", "f2": 1730.91, "f3": 1.11})

        self.assertEqual(quote, BoardQuote("BK0800", "人工智能", 1.11, 1730.91))

    def test_board_page_urls_use_delay_host_and_page_number(self) -> None:
        urls = _board_page_urls("m:90+t:3", 2)

        self.assertIn("push2delay.eastmoney.com", urls[0])
        self.assertIn("pn=2", urls[0])
        self.assertIn("fs=m:90+t:3", urls[0])

    def test_parse_direct_board_quote_data_reads_scaled_values(self) -> None:
        quote = _parse_board_quote_data(
            {
                "f57": "BK0800",
                "f58": "人工智能",
                "f43": 173091,
                "f170": 111,
            }
        )

        self.assertEqual(quote, BoardQuote("BK0800", "人工智能", 1.11, 1730.91))


if __name__ == "__main__":
    unittest.main()
