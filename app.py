import os
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

PYTHON_DIR = Path(sys.executable).resolve().parent
TCL_DIR = PYTHON_DIR / "Library" / "lib" / "tcl8.6"
TK_DIR = PYTHON_DIR / "Library" / "lib" / "tk8.6"
if TCL_DIR.exists():
    os.environ.setdefault("TCL_LIBRARY", str(TCL_DIR))
if TK_DIR.exists():
    os.environ.setdefault("TK_LIBRARY", str(TK_DIR))

from stock_floater.windowing import acquire_single_instance, enable_windows_dpi_awareness


enable_windows_dpi_awareness()
if not acquire_single_instance("Local\\AStockFloaterSingleInstance"):
    sys.exit(0)

from stock_floater.ui import StockFloaterApp


if __name__ == "__main__":
    StockFloaterApp().run()
