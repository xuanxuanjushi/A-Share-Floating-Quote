import json
import subprocess
from dataclasses import dataclass
from urllib.request import Request, urlopen

from .config import BoardItem, StockItem, normalize_board_code, normalize_stock_code
from .market_data import BoardQuote, StockQuote, _fetch_text_with_powershell


EASTMONEY_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&beg=20200101&end=20500101"
)
EASTMONEY_TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&iscca=0&ndays=1"
)
SINA_GOLD_DAILY_URL = "https://stock.finance.sina.com.cn/futures/api/openapi.php/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU"
SINA_GOLD_MINUTE_URL = "https://stock.finance.sina.com.cn/futures/api/openapi.php/GlobalFuturesService.getGlobalFuturesMinLine?symbol=XAU"
TENCENT_DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
TENCENT_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
MARKET_CHART_SECIDS = {
    "gold": "sina:XAU",
    "shanghai": "1.000001",
    "brent": "112.B00Y",
    "nasdaq": "100.NDX",
}


@dataclass(frozen=True)
class ChartTarget:
    title: str
    secid: str
    message: str = ""


@dataclass(frozen=True)
class ChartPoint:
    label: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0


@dataclass(frozen=True)
class ChartSeries:
    title: str
    points: list[ChartPoint]
    message: str = ""
    percent_base: float = 0.0


def stock_chart_target(item: StockItem, quote: StockQuote | None = None) -> ChartTarget:
    code = normalize_stock_code(item.code or (quote.code if quote else ""))
    title = item.name or (quote.name if quote else "") or code or "标的"
    secid = _stock_secid(code)
    if not secid:
        return ChartTarget(title, "", "这个标的暂时缺少代码，无法显示图表。")
    return ChartTarget(title, secid)


def board_chart_target(item: BoardItem, quote: BoardQuote | None = None) -> ChartTarget:
    code = normalize_board_code(item.code or (quote.code if quote else ""))
    title = item.name or (quote.name if quote else "") or code or "板块"
    if not code:
        return ChartTarget(title, "", "这个板块暂时缺少代码，无法显示图表。")
    return ChartTarget(title, f"90.{code}")


def market_chart_target(key: str, name: str) -> ChartTarget:
    secid = MARKET_CHART_SECIDS.get(key, "")
    if not secid:
        return ChartTarget(name or key, "", "这个市场项暂时没有稳定的免费图表数据。")
    return ChartTarget(name or key, secid)


def fetch_chart_series(target: ChartTarget, mode: str, timeout: float = 8.0) -> ChartSeries:
    if not target.secid:
        return ChartSeries(target.title, [], target.message or "暂无图表数据")
    try:
        if target.secid.startswith("sina:XAU"):
            payload = _fetch_sina_json(_sina_gold_url(mode), timeout)
            if mode == "intraday":
                return parse_sina_gold_minute_payload(target.title, payload)
            return parse_sina_gold_daily_payload(target.title, payload)
        payload = _fetch_json(_chart_url(target.secid, mode), timeout)
        if mode == "intraday":
            series = parse_trends_payload(target.title, payload)
        else:
            series = parse_kline_payload(target.title, payload)
        if series.points:
            return series
    except Exception:
        series = None
    fallback = _fetch_tencent_series(target, mode, timeout)
    if fallback:
        return fallback
    if series:
        return series
    return ChartSeries(target.title, [], "图表暂不可用，稍后自动重试。")


def parse_kline_payload(title: str, payload: dict) -> ChartSeries:
    data = payload.get("data") or {}
    name = str(data.get("name") or title).strip() or title
    points: list[ChartPoint] = []
    for raw in (data.get("klines") or [])[-60:]:
        parts = str(raw).split(",")
        if len(parts) < 5:
            continue
        date = parts[0]
        open_price = _safe_float(parts[1])
        close = _safe_float(parts[2])
        high = _safe_float(parts[3])
        low = _safe_float(parts[4])
        volume = _safe_float(parts[5]) if len(parts) > 5 else 0.0
        if None in {open_price, close, high, low}:
            continue
        points.append(ChartPoint(date[-5:], open_price, close, high, low, volume or 0.0))
    message = "" if points else "暂无日 K 数据"
    return ChartSeries(name, points, message)


def parse_trends_payload(title: str, payload: dict) -> ChartSeries:
    data = payload.get("data") or {}
    name = str(data.get("name") or title).strip() or title
    percent_base = _safe_float(data.get("prePrice") or data.get("preClose") or data.get("pre_price")) or 0.0
    points: list[ChartPoint] = []
    for raw in data.get("trends") or []:
        parts = str(raw).split(",")
        if len(parts) < 2:
            continue
        time_label = parts[0][-5:]
        if not _should_keep_intraday_label(time_label):
            continue
        price = _safe_float(parts[1])
        if price is None:
            continue
        volume = _safe_float(parts[2]) if len(parts) > 2 else 0.0
        points.append(ChartPoint(time_label, price, price, price, price, volume or 0.0))
    message = "" if points else "暂无分时数据"
    return ChartSeries(name, points, message, percent_base)


def parse_tencent_daily_payload(title: str, symbol: str, payload: dict) -> ChartSeries:
    data = (payload.get("data") or {}).get(symbol) or {}
    name = _tencent_name(title, symbol, data)
    rows = data.get("qfqday") or data.get("day") or []
    points: list[ChartPoint] = []
    for raw in rows[-60:]:
        parts = raw if isinstance(raw, list) else str(raw).split(" ")
        if len(parts) < 5:
            continue
        date = str(parts[0])
        open_price = _safe_float(parts[1])
        close = _safe_float(parts[2])
        high = _safe_float(parts[3])
        low = _safe_float(parts[4])
        volume = _safe_float(parts[5]) if len(parts) > 5 else 0.0
        if not date or None in {open_price, close, high, low}:
            continue
        points.append(ChartPoint(date[-5:], open_price, close, high, low, volume or 0.0))
    message = "" if points else "暂无日 K 数据"
    return ChartSeries(name, points, message)


def parse_tencent_minute_payload(title: str, symbol: str, payload: dict) -> ChartSeries:
    data = (payload.get("data") or {}).get(symbol) or {}
    name = _tencent_name(title, symbol, data)
    percent_base = _tencent_previous_close(symbol, data)
    minute_data = data.get("data") or {}
    rows = minute_data.get("data") if isinstance(minute_data, dict) else minute_data
    points: list[ChartPoint] = []
    for raw in rows or []:
        parts = raw if isinstance(raw, list) else str(raw).split()
        if len(parts) < 2:
            continue
        label = _tencent_minute_label(str(parts[0]))
        if not _should_keep_intraday_label(label):
            continue
        price = _safe_float(parts[1])
        volume = _safe_float(parts[2]) if len(parts) > 2 else 0.0
        if not label or price is None:
            continue
        points.append(ChartPoint(label, price, price, price, price, volume or 0.0))
    message = "" if points else "暂无分时数据"
    return ChartSeries(name, points, message, percent_base)


def parse_sina_gold_daily_payload(title: str, payload: dict) -> ChartSeries:
    rows = payload.get("result", {}).get("data") or []
    points: list[ChartPoint] = []
    for row in rows[-60:]:
        date = str(row.get("date") or "")
        open_price = _safe_float(row.get("open"))
        close = _safe_float(row.get("close"))
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        if not date or None in {open_price, close, high, low}:
            continue
        points.append(ChartPoint(date[-5:], open_price, close, high, low))
    message = "" if points else "暂无黄金日 K 数据"
    return ChartSeries(title, points, message)


def parse_sina_gold_minute_payload(title: str, payload: dict) -> ChartSeries:
    rows = payload.get("result", {}).get("data", {}).get("minLine_1d") or []
    points: list[ChartPoint] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        label = _sina_gold_minute_label(row)
        price = _safe_float(row[1])
        if not label or price is None:
            continue
        points.append(ChartPoint(label, price, price, price, price))
    message = "" if points else "暂无黄金分时数据"
    percent_base = points[0].close if points else 0.0
    return ChartSeries(title, points, message, percent_base)


def _chart_url(secid: str, mode: str) -> str:
    if mode == "intraday":
        return EASTMONEY_TRENDS_URL.format(secid=secid)
    return EASTMONEY_KLINE_URL.format(secid=secid)


def _sina_gold_url(mode: str) -> str:
    if mode == "intraday":
        return SINA_GOLD_MINUTE_URL
    return SINA_GOLD_DAILY_URL


def _sina_gold_minute_label(row: list) -> str:
    first = str(row[0]) if row else ""
    if ":" in first:
        return first[-5:]
    if len(row) > 4:
        return str(row[4])[-5:]
    return ""


def _fetch_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return json.loads(_fetch_text_with_curl(url, timeout))


def _fetch_sina_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return json.loads(_fetch_text_with_curl(url, timeout, referer="https://finance.sina.com.cn/"))


def _fetch_text_with_curl(url: str, timeout: float, referer: str = "https://quote.eastmoney.com/") -> str:
    completed = subprocess.run(
        [
            "curl.exe",
            "-sS",
            "-L",
            "--max-time",
            str(max(3, int(timeout))),
            "-H",
            f"Referer: {referer}",
            "-H",
            "User-Agent: Mozilla/5.0",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 3,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "curl 请求失败")
    return completed.stdout.strip()


def _fetch_tencent_series(target: ChartTarget, mode: str, timeout: float) -> ChartSeries | None:
    symbol = _tencent_symbol_from_secid(target.secid)
    if not symbol:
        return None
    try:
        if mode == "intraday":
            payload = _fetch_tencent_json(TENCENT_MINUTE_URL.format(symbol=symbol), timeout)
            series = parse_tencent_minute_payload(target.title, symbol, payload)
        else:
            payload = _fetch_tencent_json(TENCENT_DAILY_URL.format(symbol=symbol, count=120), timeout)
            series = parse_tencent_daily_payload(target.title, symbol, payload)
    except Exception:
        return None
    return series if series.points else None


def _fetch_tencent_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return json.loads(_fetch_text_with_curl(url, timeout, referer="https://gu.qq.com/"))


def _tencent_symbol_from_secid(secid: str) -> str:
    market, _, code = secid.partition(".")
    if len(code) != 6 or not code.isdigit():
        return ""
    if market == "1":
        return f"sh{code}"
    if market == "0":
        return f"sz{code}"
    return ""


def _tencent_name(title: str, symbol: str, data: dict) -> str:
    qt = data.get("qt") or {}
    quote = qt.get(symbol) if isinstance(qt, dict) else None
    if isinstance(quote, list) and len(quote) > 1:
        return str(quote[1] or title).strip() or title
    return title


def _tencent_previous_close(symbol: str, data: dict) -> float:
    qt = data.get("qt") or {}
    quote = qt.get(symbol) if isinstance(qt, dict) else None
    if isinstance(quote, list) and len(quote) > 4:
        return _safe_float(quote[4]) or 0.0
    return 0.0


def _tencent_minute_label(value: str) -> str:
    if ":" in value:
        return value[-5:]
    if len(value) >= 4 and value[:4].isdigit():
        return f"{value[:2]}:{value[2:4]}"
    return ""


def _should_keep_intraday_label(label: str) -> bool:
    minute = _minute_of_day(label)
    if minute is None:
        return True
    if 9 * 60 <= minute <= 15 * 60 + 30:
        return 9 * 60 + 30 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 15 * 60
    return True


def _minute_of_day(label: str) -> int | None:
    value = str(label).strip()
    if ":" in value:
        hour_text, minute_text = value[-5:].split(":", 1)
    elif len(value) >= 4 and value[-4:].isdigit():
        hour_text, minute_text = value[-4:-2], value[-2:]
    else:
        return None
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _stock_secid(code: str) -> str:
    clean = normalize_stock_code(code)
    raw_code = clean[2:] if clean[:2] in {"sh", "sz", "bj"} else clean
    if not raw_code:
        return ""
    if clean.startswith("sh") or raw_code.startswith(("6", "9")):
        return f"1.{raw_code}"
    return f"0.{raw_code}"


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
