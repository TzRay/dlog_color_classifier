"""GUI 冒烟测试。

有 PySide6 或 PyQt5 时验证主窗口能创建，并能从扫描结果生成计划。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from dji_color_classifier.core.models import (
    ClassificationEvidence,
    ColorMode,
    ExecutionRecord,
    PlanAction,
    PlanItem,
    ScanResult,
)


def load_gui(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """加载当前环境可用的 Qt；普通 CI 未安装 GUI 依赖时统一跳过。"""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    if os.name == "nt" and importlib.util.find_spec("PyQt5") is not None:
        # 本地 Windows 环境的 PySide6 DLL 可能与系统运行库冲突，优先使用已安装的 PyQt5。
        monkeypatch.setenv("DJI_COLOR_QT_BINDING", "PyQt5")
    try:
        from dji_color_classifier.gui.main_window import MainWindow
        from dji_color_classifier.gui.qt_compat import QApplication
    except RuntimeError as exc:
        pytest.skip(str(exc))
    return QApplication, MainWindow


def test_gui_builds_plan_offscreen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI 应能基于结果表生成整理计划。"""

    QApplication, MainWindow = load_gui(monkeypatch)

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


def test_gui_lists_hlg_filter_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI 应能筛选并统计新增的 Rec.2100 HLG（HDR）模式。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_HLG.MP4"
    source.write_bytes(b"")
    window = MainWindow()
    result = ScanResult(source, ColorMode.REC2100_HLG, ClassificationEvidence(None, None))
    window.results = [result]
    window._update_summary(window.results)

    assert window.mode_filter.findData("rec2100_hlg") >= 0
    assert window.metric_labels["Rec.2100 HLG（HDR）"].text() == "1"
    app.processEvents()


def test_gui_plan_rows_stay_aligned_with_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """插入伴随文件后，表格每一行仍必须与对应计划项一致。"""

    QApplication, MainWindow = load_gui(monkeypatch)

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

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window._scan_token = 2
    stale = ScanResult(tmp_path / "old.mp4", ColorMode.DLOG, ClassificationEvidence(2, None))

    window.on_scan_finished((1, tmp_path, [stale]))

    assert window.results == []
    app.processEvents()


def test_single_page_shows_safe_action_after_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描完成后应在同一页直接显示安全的默认整理操作。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    window = MainWindow()
    window.path_edit.setText(str(tmp_path))
    window._scan_token = 1
    window._scan_root = tmp_path.resolve()
    result = ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))

    window.on_scan_finished((1, tmp_path.resolve(), [result]))

    assert window.plan == []
    assert window.apply_button.isEnabled()
    assert window.mode_combo.currentData() == "copy"
    assert "复制并整理" in window.apply_button.text()
    assert not window.results_panel.isHidden()
    assert window.details_panel.isHidden()
    assert not hasattr(window, "pages")
    assert not hasattr(window, "plan_button")
    app.processEvents()


def test_path_change_invalidates_running_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描过程中路径变化时，旧目录结果不得进入当前界面。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    stale = ScanResult(first_dir / "old.mp4", ColorMode.DLOG, ClassificationEvidence(2, None))
    window = MainWindow()
    window.path_edit.setText(str(first_dir))
    window._scan_token = 1
    window._scan_root = first_dir.resolve()
    window._busy = True
    window._busy_kind = "scan"

    window.path_edit.setText(str(second_dir))
    window.on_scan_finished((1, first_dir.resolve(), [stale]))

    assert window.results == []
    assert window._scan_root is None
    assert window._scan_token == 2
    assert not window._busy
    app.processEvents()


def test_completed_operation_cannot_be_reenabled_by_mode_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整理完成后切换方式不能重新激活旧结果的执行按钮。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    window = MainWindow()
    window.results = [ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))]
    window._execution_completed = True

    window.mode_combo.setCurrentIndex(window.mode_combo.findData("move"))

    assert not window.apply_button.isEnabled()
    assert "重新识别" in window.action_status_label.text()
    app.processEvents()


def test_stale_scan_error_does_not_interrupt_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧扫描任务的错误不得打断编号相同的当前执行任务。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window._scan_token = 2
    window._execution_token = 1
    window._busy = True
    window._busy_kind = "execution"

    window.on_task_failed(("scan", 1, "旧扫描失败"))

    assert window._busy
    assert window._busy_kind == "execution"
    assert "忽略已过期的扫描任务错误" in window.log.toPlainText()
    app.processEvents()


def test_skipped_execution_row_keeps_skipped_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """执行器把跳过记录标记为成功时，界面仍应明确显示“跳过”。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    source = tmp_path / "DJI_0001.MP4"
    target = tmp_path / "dlog2" / source.name
    result = ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))
    item = PlanItem(source, target, PlanAction.COPY, result, skipped=True, reason="目标文件已存在")
    record = ExecutionRecord(source, target, PlanAction.COPY, ColorMode.DLOG2, True, "目标文件已存在")
    window = MainWindow()
    window.model.set_plan([item])
    window.model.set_execution_records([record])

    assert window.model.data(window.model.index(0, 0)) == "跳过"
    app.processEvents()


def test_start_scan_resets_old_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重新识别目录时应清除旧筛选，避免筛选条件与表格内容不一致。"""

    QApplication, MainWindow = load_gui(monkeypatch)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.thread_pool = type("ImmediatePool", (), {"start": lambda self, worker: None})()
    window.path_edit.setText(str(tmp_path))
    window.status_filter.setCurrentIndex(window.status_filter.findData("attention"))
    window.mode_filter.setCurrentIndex(window.mode_filter.findData("dlog"))
    window.search_edit.setText("DJI")

    window.start_scan()

    assert window.status_filter.currentData() == "all"
    assert window.mode_filter.currentData() == "all"
    assert window.search_edit.text() == ""
    app.processEvents()


def test_cancelled_confirmation_restores_filterable_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消最终确认后应丢弃临时计划并恢复可筛选的识别结果。"""

    QApplication, MainWindow = load_gui(monkeypatch)
    import dji_color_classifier.gui.main_window as main_window

    app = QApplication.instance() or QApplication(sys.argv)
    main_window.QMessageBox.question = staticmethod(lambda *args, **kwargs: 0)
    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    result = ScanResult(source, ColorMode.DLOG2, ClassificationEvidence(22, None))
    window = MainWindow()
    window.path_edit.setText(str(tmp_path))
    window._scan_root = tmp_path.resolve()
    window._operation_root = tmp_path.resolve()
    window.results = [result]
    window.model.set_results([result])
    window._update_organize_action()

    window.apply_current_plan()

    assert window.plan == []
    assert window.model.rows == [result]
    assert window.status_filter.isEnabled()
    assert window.mode_filter.isEnabled()
    assert window.search_edit.isEnabled()
    app.processEvents()
