import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stock_floater.chart_data import (
    fetch_chart_series,
    board_chart_target,
    market_chart_target,
    parse_kline_payload,
    parse_sina_gold_daily_payload,
    parse_sina_gold_minute_payload,
    parse_tencent_daily_payload,
    parse_tencent_minute_payload,
    parse_trends_payload,
    stock_chart_target,
)
from stock_floater.config import BoardItem, StockItem
from stock_floater.market_data import BoardQuote, StockQuote


class ChartDataTests(unittest.TestCase):
    def test_resolves_stock_and_board_targets(self) -> None:
        stock = stock_chart_target(StockItem(code="600519", name="贵州茅台"), StockQuote("sh600519", "贵州茅台", 1.2))
        board = board_chart_target(BoardItem(code="BK0800", name="人工智能"), BoardQuote("BK0800", "人工智能", 2.3))

        self.assertEqual(stock.secid, "1.600519")
        self.assertEqual(stock.title, "贵州茅台")
        self.assertEqual(board.secid, "90.BK0800")
        self.assertEqual(board.title, "人工智能")

    def test_resolves_market_targets_with_known_chart_sources(self) -> None:
        self.assertEqual(market_chart_target("shanghai", "上证").secid, "1.000001")
        self.assertEqual(market_chart_target("brent", "布伦特原油").secid, "112.B00Y")
        self.assertEqual(market_chart_target("nasdaq", "纳指").secid, "100.NDX")
        self.assertEqual(market_chart_target("gold", "黄金").secid, "sina:XAU")

    def test_parses_daily_kline_payload(self) -> None:
        payload = {
            "data": {
                "name": "贵州茅台",
                "klines": [
                    "2026-07-13,1200.00,1210.00,1220.00,1190.00,10000",
                    "2026-07-14,1210.00,1230.00,1240.00,1205.00,12000",
                ],
            }
        }

        series = parse_kline_payload("贵州茅台", payload)

        self.assertEqual(series.title, "贵州茅台")
        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.points[-1].label, "07-14")
        self.assertEqual(series.points[-1].close, 1230.0)
        self.assertEqual(series.points[-1].high, 1240.0)
        self.assertEqual(series.points[-1].volume, 12000.0)

    def test_parses_tencent_daily_payload(self) -> None:
        payload = {
            "data": {
                "sh512010": {
                    "qt": {"sh512010": ["", "医药ETF易方达"]},
                    "qfqday": [
                        ["2026-07-13", "0.360", "0.365", "0.370", "0.358", "10100"],
                        ["2026-07-14", "0.365", "0.372", "0.374", "0.362", "12000"],
                    ],
                }
            }
        }

        series = parse_tencent_daily_payload("医药ETF易方达", "sh512010", payload)

        self.assertEqual(series.title, "医药ETF易方达")
        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.points[-1].label, "07-14")
        self.assertEqual(series.points[-1].close, 0.372)
        self.assertEqual(series.points[-1].volume, 12000.0)

    def test_parses_intraday_trends_payload(self) -> None:
        payload = {
            "data": {
                "name": "贵州茅台",
                "prePrice": 1200.0,
                "trends": [
                    "2026-07-14 09:30,1201.00,1201.00,100,120100",
                    "2026-07-14 09:31,1203.50,1202.25,120,144420",
                    "2026-07-14 15:30,1203.50,1202.25,120,144420",
                ],
            }
        }

        series = parse_trends_payload("贵州茅台", payload)

        self.assertEqual(series.title, "贵州茅台")
        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.percent_base, 1200.0)
        self.assertEqual(series.points[0].label, "09:30")
        self.assertEqual(series.points[-1].close, 1203.5)

    def test_parses_tencent_intraday_with_previous_close_and_filters_late_tail(self) -> None:
        payload = {
            "data": {
                "sh600988": {
                    "qt": {"sh600988": ["1", "赤峰黄金", "600988", "35.48", "32.25"]},
                    "data": {
                        "data": [
                            "0930 33.10 3466 11472460.00",
                            "1130 34.00 10000 34000000.00",
                            "1500 35.48 811912 2782582595.00",
                            "1530 35.48 811985 2782842044.54",
                        ]
                    },
                }
            }
        }

        series = parse_tencent_minute_payload("赤峰黄金", "sh600988", payload)

        self.assertEqual(series.title, "赤峰黄金")
        self.assertEqual(series.percent_base, 32.25)
        self.assertEqual([point.label for point in series.points], ["09:30", "11:30", "15:00"])
        self.assertEqual(series.points[-1].close, 35.48)

    def test_parses_sina_gold_daily_payload(self) -> None:
        payload = {
            "result": {
                "data": [
                    {"date": "2026-07-13", "open": "3980.10", "high": "4012.30", "low": "3972.20", "close": "4000.80"},
                    {"date": "2026-07-14", "open": "4004.30", "high": "4102.82", "low": "3983.56", "close": "4085.11"},
                ]
            }
        }

        series = parse_sina_gold_daily_payload("黄金", payload)

        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.points[-1].label, "07-14")
        self.assertEqual(series.points[-1].close, 4085.11)

    def test_parses_sina_gold_minute_payload(self) -> None:
        payload = {
            "result": {
                "data": {
                    "minLine_1d": [
                        ["2026-07-14", "4000.800", "LIFFE", "", "06:00", "4005.560"],
                        ["06:01", "4004.530", "0", "0", "4004.367", "2026-07-14 06:01:00"],
                    ]
                }
            }
        }

        series = parse_sina_gold_minute_payload("黄金", payload)

        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.points[0].label, "06:00")
        self.assertEqual(series.points[-1].close, 4004.53)

    def test_fetch_chart_series_falls_back_to_curl_when_urlopen_is_rejected(self) -> None:
        target = stock_chart_target(StockItem(code="sh600519", name="贵州茅台"))
        payload = '{"data":{"name":"贵州茅台","klines":["2026-07-14,1210.00,1230.00,1240.00,1205.00,12000"]}}'

        with patch("stock_floater.chart_data.urlopen", side_effect=ConnectionError("closed")):
            with patch("stock_floater.chart_data._fetch_text_with_curl", return_value=payload):
                series = fetch_chart_series(target, "daily")

        self.assertEqual(len(series.points), 1)
        self.assertEqual(series.points[0].close, 1230.0)

    def test_fetch_daily_chart_falls_back_to_tencent_when_eastmoney_fails(self) -> None:
        target = stock_chart_target(StockItem(code="sz512010", name="医药ETF易方达"))
        payload = {
            "data": {
                "sh512010": {
                    "qt": {"sh512010": ["", "医药ETF易方达"]},
                    "qfqday": [["2026-07-14", "0.365", "0.372", "0.374", "0.362", "12000"]],
                }
            }
        }

        with patch("stock_floater.chart_data._fetch_json", side_effect=RuntimeError("eastmoney failed")):
            with patch("stock_floater.chart_data._fetch_tencent_json", return_value=payload):
                series = fetch_chart_series(target, "daily")

        self.assertEqual(target.secid, "1.512010")
        self.assertEqual(len(series.points), 1)
        self.assertEqual(series.points[0].close, 0.372)

    def test_fetch_chart_series_hides_raw_powershell_error_from_ui(self) -> None:
        target = stock_chart_target(StockItem(code="sh600519", name="贵州茅台"))
        raw_error = (
            "Invoke-WebRequest : 请求失败\\n"
            "C:\\Users\\Administrator\\AppData\\Local\\Temp\\tmpabc.ps1:5 字符: 2\\n"
            "+ Invoke-WebRequest -Uri $Url -Headers @{Referer='https://quote.eastmo ...\\n"
            "FullyQualifiedErrorId : WebCmdletWebResponseException"
        )

        with patch("stock_floater.chart_data.urlopen", side_effect=ConnectionError("closed")):
            with patch("stock_floater.chart_data._fetch_text_with_curl", side_effect=RuntimeError("curl failed")):
                with patch("stock_floater.chart_data._fetch_text_with_powershell", side_effect=RuntimeError(raw_error)):
                    series = fetch_chart_series(target, "daily")

        self.assertEqual(series.message, "图表暂不可用，稍后自动重试。")
        self.assertNotIn("Invoke-WebRequest", series.message)
        self.assertNotIn("AppData", series.message)


if __name__ == "__main__":
    unittest.main()
