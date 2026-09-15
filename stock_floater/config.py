import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent.parent
APPDATA_CONFIG_PATH = Path(os.environ.get("APPDATA", APP_DIR)) / "AStockFloater" / "settings.json"
PROJECT_CONFIG_PATH = APP_DIR / "config" / "settings.json"
CONFIG_PATH = APPDATA_CONFIG_PATH if getattr(sys, "frozen", False) else PROJECT_CONFIG_PATH
DEFAULT_WINDOW_X = 80
DEFAULT_WINDOW_Y = 80


@dataclass
class StockItem:
    code: str = ""
    name: str = ""
    enabled: bool = True
    name_hidden: bool = False
    price_hidden: bool = False

    def has_content(self) -> bool:
        return bool(self.code or self.name)


@dataclass
class BoardItem:
    code: str = ""
    name: str = ""
    enabled: bool = True
    name_hidden: bool = False
    price_hidden: bool = False

    def has_content(self) -> bool:
        return bool(self.code or self.name)


@dataclass
class AppSettings:
    stocks: list[StockItem] = field(default_factory=list)
    boards: list[BoardItem] = field(default_factory=list)
    show_gold: bool = True
    show_brent: bool = True
    show_nasdaq: bool = True
    show_shanghai: bool = True
    opacity: float = 0.88
    scale: float = 0.82
    refresh_seconds: int = 2
    window_x: int = 80
    window_y: int = 80
    chart_width: int = 360
    chart_height: int = 220


def normalize_stock_code(code: str) -> str:
    clean = code.strip().lower().replace(".", "").replace("_", "")
    if not clean:
        return ""
    prefix = clean[:2] if clean[:2] in {"sh", "sz", "bj"} else ""
    raw_code = clean[2:] if prefix else clean
    if len(raw_code) == 6 and raw_code.isdigit():
        if raw_code.startswith("5"):
            return f"sh{raw_code}"
        if raw_code.startswith(("159", "16", "18")):
            return f"sz{raw_code}"
        if raw_code.startswith(("6", "9")):
            return f"sh{raw_code}"
        if raw_code.startswith(("0", "2", "3")):
            return f"sz{raw_code}"
        if raw_code.startswith(("4", "8")):
            return f"bj{raw_code}"
    if prefix:
        return f"{prefix}{raw_code}"
    return clean


def normalize_board_code(code: str) -> str:
    clean = code.strip().upper().replace(".", "").replace("_", "")
    if not clean:
        return ""
    if clean.startswith("BK"):
        return clean
    if clean.isdigit():
        return f"BK{clean}"
    return clean


def _stock_from_dict(raw: dict[str, Any]) -> StockItem:
    return StockItem(
        code=normalize_stock_code(str(raw.get("code", ""))),
        name=str(raw.get("name", "")).strip(),
        enabled=bool(raw.get("enabled", True)),
        name_hidden=bool(raw.get("name_hidden", False)),
        price_hidden=bool(raw.get("price_hidden", False)),
    )


def _board_from_dict(raw: dict[str, Any]) -> BoardItem:
    return BoardItem(
        code=normalize_board_code(str(raw.get("code", ""))),
        name=str(raw.get("name", "")).strip(),
        enabled=bool(raw.get("enabled", True)),
        name_hidden=bool(raw.get("name_hidden", False)),
        price_hidden=bool(raw.get("price_hidden", False)),
    )


def load_settings() -> AppSettings:
    if not CONFIG_PATH.exists():
        return AppSettings()

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    return settings_from_dict(raw)


def settings_from_dict(raw: dict[str, Any]) -> AppSettings:
    stocks = [_stock_from_dict(item) for item in raw.get("stocks", [])]
    boards = [_board_from_dict(item) for item in raw.get("boards", [])]
    return AppSettings(
        stocks=stocks,
        boards=boards,
        show_gold=bool(raw.get("show_gold", True)),
        show_brent=bool(raw.get("show_brent", True)),
        show_nasdaq=bool(raw.get("show_nasdaq", True)),
        show_shanghai=bool(raw.get("show_shanghai", True)),
        opacity=max(0.35, min(1.0, float(raw.get("opacity", 0.88)))),
        scale=max(0.5, min(1.8, float(raw.get("scale", 0.82)))),
        refresh_seconds=max(1, int(raw.get("refresh_seconds", 2))),
        window_x=_visible_position(raw.get("window_x"), DEFAULT_WINDOW_X),
        window_y=_visible_position(raw.get("window_y"), DEFAULT_WINDOW_Y),
        chart_width=max(240, min(900, _visible_position(raw.get("chart_width"), 360))),
        chart_height=max(147, min(550, _visible_position(raw.get("chart_height"), 220))),
    )


def _visible_position(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def save_settings(settings: AppSettings) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(settings_to_dict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    data = asdict(settings)
    data["stocks"] = [asdict(item) for item in settings.stocks if item.has_content()]
    data["boards"] = [asdict(item) for item in settings.boards if item.has_content()]
    return data


def export_settings(settings: AppSettings, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(settings_to_dict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def import_settings(path: str | Path) -> AppSettings:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("配置文件格式不正确")
    return settings_from_dict(raw)
