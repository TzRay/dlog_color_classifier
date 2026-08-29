"""pywebview 桌面 Web 应用入口。

pywebview 是可选依赖；未安装时不会影响 CLI、PySide6 GUI 和核心测试，
但启动 Web 工作台会给出明确的中文安装提示。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from dji_color_classifier.web_service import ApplicationService


LOGGER = logging.getLogger(__name__)


class DesktopBridge:
    """暴露给 JavaScript 的窄接口，同时承载桌面文件选择对话框。"""

    def __init__(self, service: ApplicationService) -> None:
        """创建桥接对象。"""

        self.service = service
        self.window: Any | None = None

    def bind_window(self, window: Any) -> None:
        """绑定 pywebview 窗口，供选择目录/文件 API 使用。"""

        self.window = window

    def get_state(self) -> dict[str, Any]:
        """返回服务状态。"""

        return self.service.get_state()

    def start_scan(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """提交扫描任务。"""

        return self.service.start_scan(options)

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """读取任务状态。"""

        return self.service.get_task_status(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """取消后台任务。"""

        return self.service.cancel_task(task_id)

    def build_plan(self, options: dict[str, Any]) -> dict[str, Any]:
        """生成整理计划。"""

        return self.service.build_plan(options)

    def execute_plan(self, options: dict[str, Any]) -> dict[str, Any]:
        """提交整理任务。"""

        return self.service.execute_plan(options)

    def export_report(self, options: dict[str, Any]) -> dict[str, Any]:
        """导出识别报告。"""

        return self.service.export_report(options)

    def load_manifest(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """载入操作记录。"""

        return self.service.load_manifest(options)

    def preview_undo(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """预览撤销计划。"""

        return self.service.preview_undo(options)

    def execute_undo(self, options: dict[str, Any]) -> dict[str, Any]:
        """提交撤销任务。"""

        return self.service.execute_undo(options)

    def choose_directory(self) -> str | None:
        """打开系统目录选择器。"""

        return self._choose_file_dialog("folder")

    def choose_manifest(self) -> str | None:
        """打开 manifest 文件选择器。"""

        return self._choose_file_dialog("manifest")

    def choose_report_path(self, fmt: str = "csv") -> str | None:
        """打开报告保存对话框。"""

        return self._choose_file_dialog("report", fmt=fmt)

    def _choose_file_dialog(self, kind: str, *, fmt: str = "csv") -> str | None:
        """统一处理 pywebview 文件对话框，并把取消转换为 None。"""

        if self.window is None:
            raise RuntimeError("Web 窗口尚未就绪")
        try:
            import webview

            if kind == "folder":
                selected = self.window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
            elif kind == "manifest":
                selected = self.window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=("JSON 文件 (*.json)",),
                )
            else:
                suffix = ".json" if fmt == "json" else ".csv"
                selected = self.window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    save_filename=f"dji_color_report{suffix}",
                    file_types=("JSON 文件 (*.json)",) if fmt == "json" else ("CSV 文件 (*.csv)",),
                )
            return selected[0] if selected else None
        except Exception as exc:
            LOGGER.exception("打开文件对话框失败：%s", exc)
            raise RuntimeError(f"无法打开系统文件对话框：{exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """启动 DJI Color Desk Web 工作台。"""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        import webview
    except ImportError:
        print("未安装 pywebview，Web 工作台需要先安装：python -m pip install 'dlog-color-classifier[web]'", file=sys.stderr)
        return 2

    arguments = argv if argv is not None else sys.argv[1:]
    debug = "--debug" in arguments
    html_path = _find_html_path()
    if not html_path.is_file():
        print(f"找不到 Web 页面：{html_path}", file=sys.stderr)
        return 1

    service = ApplicationService()
    bridge = DesktopBridge(service)
    try:
        window = webview.create_window(
            "DJI Color Desk · 素材整理工作台",
            url=html_path.as_uri(),
            js_api=bridge,
            width=1440,
            height=960,
            min_size=(1040, 720),
            resizable=True,
        )
        bridge.bind_window(window)
        webview.start(debug=debug)
    finally:
        service.close()
    return 0


def _find_html_path() -> Path:
    """定位源码树或安装后的 Web 页面资源。"""

    candidates = (
        Path(__file__).resolve().parents[1] / "prototype" / "index.html",
        Path(sys.prefix) / "prototype" / "index.html",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


if __name__ == "__main__":
    raise SystemExit(main())
