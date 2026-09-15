import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import normalize_board_code, normalize_stock_code


SINA_QUOTE_URL = "https://hq.sinajs.cn/list={symbols}"
EASTMONEY_STOCK_URL = (
    "https://push2.eastmoney.com/api/qt/stock/get"
    "?secid={secid}&fields=f57,f58,f43,f170"
)
EASTMONEY_STOCK_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f43,f170",
    EASTMONEY_STOCK_URL,
)
EASTMONEY_STOCK_SUGGEST_URL = (
    "https://searchapi.eastmoney.com/api/suggest/get"
    "?input={keyword}&type=14&token=D43BF722C8E33BDC906FB84F67E4B74B"
)
EASTMONEY_STOCK_LIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:1+t:2,m:0+t:6,m:0+t:80,m:1+t:23"
    "&fields=f12,f14,f3"
)
EASTMONEY_BOARD_URLS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs={fs}&fields=f12,f14,f2,f3",
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs={fs}&fields=f12,f14,f2,f3"
)
EASTMONEY_BOARD_PAGE_LIMIT = 8
EASTMONEY_ULIST_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&secids={secids}&fields=f12,f14,f2,f3,f4"
)
EASTMONEY_COMMODITY_LIST_URL = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=80&po=1&np=1&fltt=2&fid=f3&fs=m:112&fields=f12,f14,f2,f3,f4"
)

MARKET_INDICATORS = {
    "gold": ("黄金", "hf_XAU", "sina"),
    "brent": ("布伦特原油", "B00Y", "commodity"),
    "nasdaq": ("纳指", "gb_ixic", "sina"),
    "shanghai": ("上证", "s_sh000001", "sina"),
}


@dataclass
class StockQuote:
    code: str
    name: str
    percent: float | None
    price: float | None = None
    error: str = ""


@dataclass
class BoardQuote:
    code: str
    name: str
    percent: float | None
    price: float | None = None
    error: str = ""


@dataclass
class MarketIndicator:
    key: str
    name: str
    percent: float | None
    price: float | None = None
    error: str = ""


def fetch_quotes(codes: list[str], timeout: float = 5.0) -> dict[str, StockQuote]:
    symbols = [normalize_stock_code(code) for code in codes if normalize_stock_code(code)]
    if not symbols:
        return {}

    quotes = _fetch_sina_stock_quotes(symbols, timeout)
    missing = [symbol for symbol in symbols if symbol not in quotes or quotes[symbol].percent is None]
    if not missing:
        return quotes

    fallback = _fetch_eastmoney_stock_quotes(missing, timeout)
    quotes.update(fallback)
    for symbol in symbols:
        quotes.setdefault(symbol, StockQuote(symbol, "", None, error="未取到行情"))
    return quotes


def fetch_stock_quotes(items: list[tuple[str, str]], timeout: float = 8.0) -> dict[str, StockQuote]:
    if not items:
        return {}

    result: dict[str, StockQuote] = {}
    code_items = [(normalize_stock_code(code), name.strip()) for code, name in items if normalize_stock_code(code)]
    if code_items:
        code_quotes = fetch_quotes([code for code, _name in code_items], timeout)
        for code, name in code_items:
            quote = code_quotes.get(code)
            if quote:
                result[_stock_key(code, name)] = StockQuote(
                    code,
                    name or quote.name,
                    quote.percent,
                    quote.price,
                    quote.error,
                )

    name_items = [(code.strip(), name.strip()) for code, name in items if not normalize_stock_code(code) and name.strip()]
    if name_items:
        for code, name in name_items:
            try:
                quote = _fetch_stock_quote_by_name(name, timeout)
            except Exception as exc:
                quote = StockQuote("", name, None, error=f"股票行情暂不可用：{exc}")
            if quote:
                result[_stock_key(code, name)] = StockQuote(
                    quote.code,
                    name or quote.name,
                    quote.percent,
                    quote.price,
                    quote.error,
                )
            else:
                result[_stock_key(code, name)] = StockQuote("", name, None, error="未取到股票行情")

    return result


def fetch_market_indicators(keys: list[str], timeout: float = 8.0) -> dict[str, MarketIndicator]:
    result: dict[str, MarketIndicator] = {}
    stock_secids: dict[str, str] = {}
    ulist_secids: dict[str, str] = {}
    commodity_codes: dict[str, str] = {}
    sina_symbols: dict[str, str] = {}

    for key in keys:
        indicator = MARKET_INDICATORS.get(key)
        if not indicator:
            continue
        _label, secid, source = indicator
        if source == "sina":
            sina_symbols[key] = secid
        elif source == "stock":
            stock_secids[key] = secid
        elif source == "commodity":
            commodity_codes[key] = secid
        else:
            ulist_secids[key] = secid

    fetch_jobs = []
    if sina_symbols:
        fetch_jobs.append((_fetch_market_sina_indicators, sina_symbols))
    if stock_secids:
        fetch_jobs.append((_fetch_market_stock_indicators, stock_secids))
    if ulist_secids:
        fetch_jobs.append((_fetch_market_ulist_indicators, ulist_secids))
    if commodity_codes:
        fetch_jobs.append((_fetch_market_commodity_indicators, commodity_codes))

    if fetch_jobs:
        with ThreadPoolExecutor(max_workers=len(fetch_jobs)) as executor:
            futures = [executor.submit(fetcher, payload, timeout) for fetcher, payload in fetch_jobs]
            for future in futures:
                try:
                    result.update(future.result())
                except Exception:
                    continue

    for key in keys:
        label = MARKET_INDICATORS.get(key, (key, "", ""))[0]
        result.setdefault(key, MarketIndicator(key, label, None, None, "未取到行情"))
    return result


def _fetch_market_sina_indicators(symbols_by_key: dict[str, str], timeout: float) -> dict[str, MarketIndicator]:
    if not symbols_by_key:
        return {}

    url = SINA_QUOTE_URL.format(symbols=quote(",".join(symbols_by_key.values()), safe=",_"))
    request = Request(url, headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("gbk", errors="replace")
    except Exception as exc:
        return {
            key: MarketIndicator(key, MARKET_INDICATORS.get(key, (key, "", ""))[0], None, None, f"行情暂不可用：{exc}")
            for key in symbols_by_key
        }

    values_by_symbol: dict[str, list[str]] = {}
    for line in body.splitlines():
        left, _, right = line.partition("=")
        if not right:
            continue
        symbol = left.rsplit("hq_str_", 1)[-1].strip()
        values_by_symbol[symbol] = right.strip().strip(";").strip('"').split(",")

    result: dict[str, MarketIndicator] = {}
    for key, symbol in symbols_by_key.items():
        values = values_by_symbol.get(symbol, [])
        result[key] = _parse_sina_market_data(key, symbol, values)
    return result


def _parse_sina_market_data(key: str, symbol: str, values: list[str]) -> MarketIndicator:
    label = MARKET_INDICATORS.get(key, (key, "", ""))[0]
    if not values:
        return MarketIndicator(key, label, None, None, "未取到行情")

    if symbol == "hf_XAU":
        price = _safe_float(values[0])
        previous = _safe_float(values[1])
        percent = None
        if price is not None and previous and previous > 0:
            percent = (price - previous) / previous * 100
        return MarketIndicator(key, "黄金", percent, price)

    if symbol == "hf_OIL":
        price = _safe_float(values[0])
        previous = _safe_float(values[2]) if len(values) > 2 else None
        percent = None
        if price is not None and previous and previous > 0:
            percent = (price - previous) / previous * 100
        return MarketIndicator(key, "布油", percent, price)

    if symbol == "gb_ixic":
        price = _safe_float(values[1]) if len(values) > 1 else None
        percent = _safe_float(values[2]) if len(values) > 2 else None
        return MarketIndicator(key, "纳指", percent, price)

    if symbol == "s_sh000001":
        price = _safe_float(values[1]) if len(values) > 1 else None
        percent = _safe_float(values[3]) if len(values) > 3 else None
        return MarketIndicator(key, "上证", percent, price)

    return MarketIndicator(key, label, None, None, "行情格式未知")


def _fetch_market_stock_indicators(secids_by_key: dict[str, str], timeout: float) -> dict[str, MarketIndicator]:
    result: dict[str, MarketIndicator] = {}
    for key, secid in secids_by_key.items():
        request = Request(
            EASTMONEY_STOCK_URL.format(secid=secid),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            result[key] = _parse_eastmoney_quote_data(key, payload.get("data") or {})
        except Exception as exc:
            label = MARKET_INDICATORS.get(key, (key, "", ""))[0]
            result[key] = MarketIndicator(key, label, None, None, f"行情暂不可用：{exc}")
    return result


def _fetch_market_ulist_indicators(secids_by_key: dict[str, str], timeout: float) -> dict[str, MarketIndicator]:
    if not secids_by_key:
        return {}

    reverse = {secid: key for key, secid in secids_by_key.items()}
    request = Request(
        EASTMONEY_ULIST_URL.format(secids=quote(",".join(secids_by_key.values()), safe=",.")).replace("%2C", ","),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {
            key: MarketIndicator(key, MARKET_INDICATORS.get(key, (key, "", ""))[0], None, None, f"行情暂不可用：{exc}")
            for key in secids_by_key
        }

    result: dict[str, MarketIndicator] = {}
    rows = payload.get("data", {}).get("diff", []) or []
    for secid, key in reverse.items():
        code = secid.split(".", 1)[-1]
        row = next((item for item in rows if str(item.get("f12", "")).strip() == code), None)
        if row:
            result[key] = _parse_eastmoney_ulist_data(key, row)
    return result


def _fetch_market_commodity_indicators(codes_by_key: dict[str, str], timeout: float) -> dict[str, MarketIndicator]:
    if not codes_by_key:
        return {}

    request = Request(
        EASTMONEY_COMMODITY_LIST_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        try:
            body = _fetch_text_with_powershell(EASTMONEY_COMMODITY_LIST_URL, timeout)
            payload = json.loads(body)
        except Exception as fallback_exc:
            return {
                key: MarketIndicator(
                    key,
                    MARKET_INDICATORS.get(key, (key, "", ""))[0],
                    None,
                    None,
                    f"行情暂不可用：{fallback_exc or exc}",
                )
                for key in codes_by_key
            }

    rows = payload.get("data", {}).get("diff", []) or []
    result: dict[str, MarketIndicator] = {}
    for key, code in codes_by_key.items():
        row = next((item for item in rows if str(item.get("f12", "")).strip().upper() == code.upper()), None)
        if row:
            result[key] = _parse_eastmoney_ulist_data(key, row)
    return result


def _fetch_text_with_powershell(url: str, timeout: float) -> str:
    script_file = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8")
    script_path = Path(script_file.name)
    script_file.write(
        f"""
param([string]$Url)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
(Invoke-WebRequest -Uri $Url -Headers @{{Referer='https://quote.eastmoney.com/'; 'User-Agent'='Mozilla/5.0'}} -TimeoutSec {max(3, int(timeout))}).Content
"""
    )
    script_file.close()
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-Url", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "PowerShell 请求失败")
        return completed.stdout.strip()
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def _parse_eastmoney_quote_data(key: str, data: dict) -> MarketIndicator:
    label = MARKET_INDICATORS.get(key, (key, "", ""))[0]
    name = str(data.get("f58") or label).strip()
    price = _eastmoney_scaled_number(data.get("f43"))
    percent = _eastmoney_scaled_number(data.get("f170"))
    return MarketIndicator(key, name or label, percent, price)


def _parse_eastmoney_ulist_data(key: str, data: dict) -> MarketIndicator:
    label = MARKET_INDICATORS.get(key, (key, "", ""))[0]
    name = str(data.get("f14") or label).strip()
    if key == "brent":
        name = label
    price = _safe_float(data.get("f2"))
    percent = _safe_float(data.get("f3"))
    return MarketIndicator(key, name or label, percent, price)


def _eastmoney_scaled_number(value: object) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return number / 100


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_stock_quote_by_name(name: str, timeout: float) -> StockQuote | None:
    request = Request(
        EASTMONEY_STOCK_SUGGEST_URL.format(keyword=quote(name)),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    candidates = payload.get("QuotationCodeTable", {}).get("Data", []) or []
    match = next((item for item in candidates if item.get("Name") == name and _is_supported_security(item)), None)
    if not match:
        match = next((item for item in candidates if _is_supported_security(item)), None)
    if not match:
        return None

    quote_id = str(match.get("QuoteID") or "").strip()
    if not quote_id:
        market = str(match.get("MktNum") or "").strip()
        code = str(match.get("Code") or "").strip()
        quote_id = f"{market}.{code}" if market and code else ""
    if not quote_id:
        return None

    stock_quote = _fetch_eastmoney_stock_quotes_by_secid({quote_id}, timeout).get(quote_id)
    if not stock_quote:
        return None
    return StockQuote(
        normalize_stock_code(str(match.get("Code") or stock_quote.code)),
        stock_quote.name or name,
        stock_quote.percent,
        stock_quote.price,
        stock_quote.error,
    )


def _is_supported_security(item: dict) -> bool:
    classify = str(item.get("Classify") or "")
    security_type_name = str(item.get("SecurityTypeName") or "")
    return classify in {"AStock", "Fund"} or "ETF" in security_type_name or "基金" in security_type_name


def _fetch_all_stocks(timeout: float) -> list[StockQuote]:
    request = Request(
        EASTMONEY_STOCK_LIST_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    stocks: list[StockQuote] = []
    for row in payload.get("data", {}).get("diff", []):
        raw_code = str(row.get("f12", "")).strip()
        name = str(row.get("f14", "")).strip()
        percent_raw = row.get("f3")
        if not raw_code or not name:
            continue
        try:
            percent = float(percent_raw)
        except (TypeError, ValueError):
            percent = None
        stocks.append(StockQuote(normalize_stock_code(raw_code), name, percent))
    return stocks


def _stock_key(code: str, name: str) -> str:
    return normalize_stock_code(code) or name.strip()


def _fetch_eastmoney_stock_quotes(symbols: list[str], timeout: float) -> dict[str, StockQuote]:
    result: dict[str, StockQuote] = {}
    secids: set[str] = set()
    secid_to_symbol: dict[str, str] = {}
    for symbol in symbols:
        secid = _eastmoney_stock_secid(symbol)
        if not secid:
            continue
        secids.add(secid)
        secid_to_symbol[secid] = symbol
    raw_quotes = _fetch_eastmoney_stock_quotes_by_secid(secids, timeout)
    for secid, quote in raw_quotes.items():
        symbol = secid_to_symbol.get(secid)
        if symbol:
            result[symbol] = StockQuote(symbol, quote.name, quote.percent, quote.price, quote.error)
    return result


def _fetch_eastmoney_stock_quotes_by_secid(secids: set[str], timeout: float) -> dict[str, StockQuote]:
    result: dict[str, StockQuote] = {}
    for secid in secids:
        request = Request(
            EASTMONEY_STOCK_URL.format(secid=secid),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception:
            continue

        data = payload.get("data") or {}
        name = str(data.get("f58") or "").strip()
        price_raw = data.get("f43")
        percent_raw = data.get("f170")
        try:
            price = float(price_raw) / 100
        except (TypeError, ValueError):
            price = None
        try:
            percent = float(percent_raw) / 100
        except (TypeError, ValueError):
            percent = None
        result[secid] = StockQuote(secid, name, percent, price)
    return result


def _eastmoney_stock_secid(symbol: str) -> str:
    clean = normalize_stock_code(symbol)
    code = clean[2:] if clean[:2] in {"sh", "sz", "bj"} else clean
    if not code:
        return ""
    if clean.startswith("sh"):
        return f"1.{code}"
    if clean.startswith(("sz", "bj")):
        return f"0.{code}"
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_sina_stock_quotes(symbols: list[str], timeout: float) -> dict[str, StockQuote]:
    if not symbols:
        return {}

    url = SINA_QUOTE_URL.format(symbols=quote(",".join(symbols), safe=",_"))
    request = Request(url, headers={"Referer": "https://finance.sina.com.cn/"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("gbk", errors="replace")
    except Exception as exc:  # Network errors should not crash the floating app.
        return {
            symbol: StockQuote(symbol, "", None, error=f"行情暂不可用：{exc}")
            for symbol in symbols
        }

    result: dict[str, StockQuote] = {}
    for line in body.splitlines():
        parsed = _parse_sina_line(line)
        if parsed:
            result[parsed.code] = parsed

    for symbol in symbols:
        result.setdefault(symbol, StockQuote(symbol, "", None, error="未取到行情"))
    return result


def fetch_board_quotes(items: list[tuple[str, str]], timeout: float = 8.0) -> dict[str, BoardQuote]:
    if not items:
        return {}

    result: dict[str, BoardQuote] = {}
    unresolved: list[tuple[str, str]] = []
    code_items = [(normalize_board_code(code), name.strip()) for code, name in items if normalize_board_code(code)]
    direct_quotes = _fetch_board_quotes_by_code([code for code, _name in code_items], timeout)
    for code, name in code_items:
        quote = direct_quotes.get(code.upper())
        if quote and quote.percent is not None:
            result[_board_key(code, name)] = BoardQuote(quote.code, name or quote.name, quote.percent, quote.price, quote.error)
        else:
            unresolved.append((code, name))
    unresolved.extend((code, name) for code, name in items if not normalize_board_code(code))
    if not unresolved:
        return result

    try:
        boards = _fetch_all_boards(timeout)
    except Exception as exc:
        for code, name in unresolved:
            result[_board_key(code, name)] = BoardQuote(code, name, None, error=f"板块行情暂不可用：{exc}")
        return result

    by_code = {item.code.upper(): item for item in boards if item.code}
    by_name = {item.name: item for item in boards if item.name}

    for code, name in unresolved:
        clean_code = normalize_board_code(code)
        clean_name = name.strip()
        match = by_code.get(clean_code.upper()) if clean_code else None
        if not match and clean_name:
            match = by_name.get(clean_name)
        if not match and clean_name:
            match = next(
                (item for item in boards if clean_name in item.name or item.name in clean_name),
                None,
            )
        if match:
            result[_board_key(code, name)] = match
        else:
            result[_board_key(code, name)] = BoardQuote(clean_code, clean_name, None, error="未取到板块行情")

    return result


def _fetch_board_quotes_by_code(codes: list[str], timeout: float) -> dict[str, BoardQuote]:
    result: dict[str, BoardQuote] = {}
    for code in codes:
        clean_code = normalize_board_code(code)
        if not clean_code:
            continue
        last_error = ""
        for template in EASTMONEY_STOCK_URLS:
            url = template.format(secid=f"90.{clean_code}")
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                quote = _parse_board_quote_data(payload.get("data") or {})
            except Exception as exc:
                last_error = str(exc)
                continue
            if quote:
                result[clean_code.upper()] = quote
                break
        else:
            result[clean_code.upper()] = BoardQuote(clean_code, "", None, None, last_error)
    return result


def _fetch_all_boards(timeout: float) -> list[BoardQuote]:
    boards: list[BoardQuote] = []
    for fs in ("m:90+t:2", "m:90+t:3"):
        for page in range(1, EASTMONEY_BOARD_PAGE_LIMIT + 1):
            payload = _fetch_board_page(fs, page, timeout)
            rows = (payload.get("data") or {}).get("diff", []) or []
            if not rows:
                break
            for row in rows:
                quote = _parse_board_row(row)
                if quote:
                    boards.append(quote)
    return boards


def _board_key(code: str, name: str) -> str:
    return normalize_board_code(code) or name.strip()


def _board_page_urls(fs: str, page: int) -> list[str]:
    escaped_fs = quote(fs, safe=":+")
    return [template.format(fs=escaped_fs, page=page) for template in EASTMONEY_BOARD_URLS]


def _fetch_board_page(fs: str, page: int, timeout: float) -> dict:
    last_error: Exception | None = None
    for url in _board_page_urls(fs, page):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return {}


def _parse_board_row(row: dict) -> BoardQuote | None:
    code = str(row.get("f12", "")).strip()
    name = str(row.get("f14", "")).strip()
    if not code or not name:
        return None
    return BoardQuote(code, name, _safe_float(row.get("f3")), _safe_float(row.get("f2")))


def _parse_board_quote_data(data: dict) -> BoardQuote | None:
    code = str(data.get("f57", "")).strip()
    name = str(data.get("f58", "")).strip()
    if not code or not name:
        return None
    return BoardQuote(code, name, _eastmoney_scaled_number(data.get("f170")), _eastmoney_scaled_number(data.get("f43")))


def _parse_sina_line(line: str) -> StockQuote | None:
    if "hq_str_" not in line:
        return None

    left, _, right = line.partition("=")
    code = left.rsplit("hq_str_", 1)[-1].strip()
    values = right.strip().strip(";").strip('"').split(",")
    if len(values) < 4 or not values[0]:
        return StockQuote(code, "", None, error="返回数据为空")

    name = values[0].strip()
    try:
        previous_close = float(values[2])
        current = float(values[3])
    except ValueError:
        return StockQuote(code, name, None, error="价格格式异常")

    if previous_close <= 0:
        return StockQuote(code, name, None, error="昨收价异常")

    percent = (current - previous_close) / previous_close * 100
    return StockQuote(code, name, percent, current)
