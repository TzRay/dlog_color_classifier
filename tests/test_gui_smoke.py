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
    # 本地验收环境中的 PySide6 DLL 可能与系统运行库冲突，CI 冒烟测试固定使用稳定的 PyQt5 回退。
    monkeypatch.setenv("DJI_COLOR_QT_BINDING", "PyQt5")
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
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("move"))
    window.dir_template_edit.setText("{mode}")
    window.build_current_plan()

    assert window.plan[0].target == tmp_path / "dlog2" / "DJI_0001.MP4"
    app.processEvents()


def test_gui_plan_rows_stay_aligned_with_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """插入伴随文件后，表格每一行仍必须与对应计划项一致。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DJI_COLOR_QT_BINDING", "PyQt5")
    from dji_color_classifier.gui.qt_compat import QApplication
    from dji_color_classifier.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    first = tmp_path / "DJI_0001.MP4"
    second = tmp_path / "DJI_0002.MP4"
    sidecar = tmp_path / "DJI_0001.SRT"
    for path in (first, second, sidecar):
        path.write_bytes(b"")
    window = MainWindow()
    window.path_edit.setText(str(tmp_path))
    window.results = [
        ScanResult(first, ColorMode.DLOG, ClassificationEvidence(2, None)),
        ScanResult(second, ColorMode.DLOG2, ClassificationEvidence(22, None)),
    ]
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("move"))
    window.sidecar_check.setChecked(True)

    assert window.build_current_plan()
    assert len(window.plan) == 3
    assert [row.path for row in window.model.rows] == [item.scan_result.path for item in window.plan]
    assert window.model.rows[1].path == sidecar
    app.processEvents()


def test_gui_ignores_stale_scan_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """较旧扫描任务的结果不得覆盖当前窗口状态。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DJI_COLOR_QT_BINDING", "PyQt5")
    from dji_color_classifier.gui.qt_compat import QApplication
    from dji_color_classifier.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window._scan_token = 2
    stale = ScanResult(tmp_path / "old.mp4", ColorMode.DLOG, ClassificationEvidence(2, None))

    window.on_scan_finished((1, tmp_path, [stale]))

    assert window.results == []
    app.processEvents()


def test_organize_page_does_not_require_a_separate_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """进入第三步后应直接提供整理按钮，不再创建或要求单独的预览。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DJI_COLOR_QT_BINDING", "PyQt5")
    from dji_color_classifier.gui.qt_compat import QApplication
    from dji_color_classifier.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    window = MainWindow()
    window.path_edit.setText(str(tmp_path))
    window.results = [ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))]

    window.show_organize_page()

    assert window.plan == []
    assert window.apply_button.isEnabled()
    assert "生成" not in window.apply_button.text()
    assert not hasattr(window, "plan_button")
    app.processEvents()
