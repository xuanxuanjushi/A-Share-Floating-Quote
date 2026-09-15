from typing import TypeVar


QuoteT = TypeVar("QuoteT")


def merge_quotes_with_cache(latest: dict[str, QuoteT], cached: dict[str, QuoteT]) -> dict[str, QuoteT]:
    merged = dict(cached)
    for key, quote in latest.items():
        if _quote_has_display_value(quote) or key not in merged:
            merged[key] = quote
    return merged


def _quote_has_display_value(quote: object) -> bool:
    return getattr(quote, "percent", None) is not None
