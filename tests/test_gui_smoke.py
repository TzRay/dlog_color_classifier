"""GUI 冒烟测试。

有 PySide6 或 PyQt5 时验证主窗口能创建，并能从扫描结果生成计划。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult


def test_gui_builds_plan_offscreen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI 应能基于结果表生成整理计划。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    try:
        from dji_color_classifier.gui.qt_compat import QApplication
        from dji_color_classifier.gui.main_window import MainWindow
    except RuntimeError as exc:
        pytest.skip(str(exc))

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    window = MainWindow()
    window.path_edit.setText(str(tmp_path))
    window.results = [ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))]
    window.mode_combo.setCurrentText("move")
    window.dir_template_edit.setText("{mode}")
    window.build_current_plan()

    assert window.plan[0].target == tmp_path / "dlog2" / "DJI_0001.MP4"
    app.processEvents()
