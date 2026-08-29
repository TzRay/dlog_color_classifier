#!/usr/bin/env python3
"""v1.0 本地验收脚本。

默认使用桌面样本目录做真实扫描和 GUI 计划验收，但不会修改桌面素材。
GUI 执行路径使用临时最小 MP4 fixture 验证，临时目录退出后自动清理。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dji_color_classifier.core.scanner import scan_directory, summarize_results  # noqa: E402


def main() -> int:
    """执行本地验收。"""

    parser = argparse.ArgumentParser(description="运行 DJI Color Classifier v1.0 本地验收。")
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=Path(r"D:\Users\Ray\Desktop\新建文件夹"),
        help="真实 DJI 样本目录，默认使用桌面的新建文件夹",
    )
    args = parser.parse_args()

    sample_dir = args.sample_dir.resolve()
    if not sample_dir.is_dir():
        raise NotADirectoryError(f"样本目录不存在：{sample_dir}")

    results = scan_directory(sample_dir)
    counts = summarize_results(results)
    print(f"CLI/核心扫描：{len(results)} 个文件；{counts}")
    if not results:
        raise RuntimeError("样本目录没有可扫描视频")

    run_gui_plan_check(sample_dir)
    run_gui_execution_check()
    print("v1.0 本地验收通过")
    return 0


def run_gui_plan_check(sample_dir: Path) -> None:
    """用真实样本目录验证 GUI 扫描和生成计划。"""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dji_color_classifier.gui.main_window import MainWindow
    from dji_color_classifier.gui.qt_compat import QApplication, QT_BINDING

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.path_edit.setText(str(sample_dir))
    window.start_scan()
    wait_until(lambda: bool(window.results), app, timeout=60)

    select_combo_data(window.mode_combo, "move", "整理方式")
    select_combo_data(window.conflict_combo, "suffix", "冲突处理")
    window.dir_template_edit.setText("{mode}")
    window.build_current_plan()
    ready = len([item for item in window.plan if not item.skipped and item.target])
    if ready != len(window.results):
        raise RuntimeError(f"GUI 计划数量异常：ready={ready}, results={len(window.results)}")
    print(f"GUI 计划验收：Qt={QT_BINDING}，扫描={len(window.results)}，计划={ready}")


def run_gui_execution_check() -> None:
    """用临时 fixture 验证 GUI 执行和 manifest。"""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dji_color_classifier.gui.main_window import MainWindow
    from dji_color_classifier.gui.qt_compat import QApplication, yes_button
    from tests.test_mp4_reader import build_minimal_djmd_mp4
    import dji_color_classifier.gui.main_window as main_window

    app = QApplication.instance() or QApplication(sys.argv)
    main_window.QMessageBox.question = staticmethod(lambda *args, **kwargs: yes_button())
    main_window.QMessageBox.information = staticmethod(lambda *args, **kwargs: None)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "DJI_TEST.MP4"
        source.write_bytes(build_minimal_djmd_mp4(b"\x12\x03abc"))

        window = MainWindow()
        window.path_edit.setText(str(root))
        window.start_scan()
        wait_until(lambda: bool(window.results), app, timeout=20)
        select_combo_data(window.mode_combo, "copy", "整理方式")
        select_combo_data(window.conflict_combo, "suffix", "冲突处理")
        window.dir_template_edit.setText("{mode}")
        window.build_current_plan()
        window.apply_current_plan()

        target = root / "unknown" / "DJI_TEST.MP4"
        manifest_dir = root / ".dji-color-classifier" / "manifests"
        wait_until(lambda: target.exists() and bool(list(manifest_dir.glob("*.json"))), app, timeout=20)
        if not target.exists() or not list(manifest_dir.glob("*.json")):
            raise RuntimeError("GUI 执行验收失败：目标文件或 manifest 未生成")
        print("GUI 执行验收：临时复制和 manifest 生成成功")


def select_combo_data(combo, value: str, label: str) -> None:  # noqa: ANN001
    """按选项数据切换下拉框，并确认实际设置已经生效。"""

    index = combo.findData(value)
    if index < 0:
        raise RuntimeError(f"{label}缺少选项：{value}")
    combo.setCurrentIndex(index)
    if combo.currentData() != value:
        raise RuntimeError(f"{label}切换失败：期望 {value}，实际 {combo.currentData()}")


def wait_until(predicate, app, *, timeout: int) -> None:
    """等待 Qt 后台任务完成。"""

    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError("等待 GUI 后台任务超时")


if __name__ == "__main__":
    raise SystemExit(main())
