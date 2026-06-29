"""允许通过 `python -m dji_color_classifier` 调用 CLI。"""

from __future__ import annotations

from dji_color_classifier.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
