"""GUI 应用入口。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dji_color_classifier.gui.main_window import MainWindow
from dji_color_classifier.gui.qt_compat import QApplication


def main() -> int:
    """启动 PySide6 GUI。"""

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if "--smoke" in sys.argv:
        return 0
    if hasattr(app, "exec"):
        return app.exec()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
