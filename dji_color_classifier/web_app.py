"""pywebview 桌面 Web 应用入口。

pywebview 是可选依赖；未安装时不会影响 CLI、PySide6 GUI 和核心测试，
但启动 Web 工作台会给出明确的中文安装提示。
"""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.parse
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

    def execute_organize(self, options: dict[str, Any]) -> dict[str, Any]:
        """直接提交整理任务。"""

        return self.service.execute_organize(options)

    def export_report(self, options: dict[str, Any]) -> dict[str, Any]:
        """导出识别报告。"""

        return self.service.export_report(options)

    def choose_directory(self) -> str | None:
        """打开系统目录选择器。"""

        return self._choose_file_dialog("folder")

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


def _extract_dropped_path(event: Any) -> str | None:
    """从 pywebview drop 事件中提取文件或目录的绝对路径。

    普通浏览器的 ``File.path`` 受安全策略限制，pywebview 会在 Python 侧
    的 DOM 事件中补充 ``pywebviewFullPath``。同时兼容历史版本文档中出现的
    ``domTransfer`` 字段，便于不同 pywebview 版本运行。
    """

    if not isinstance(event, dict):
        return None
    transfer = event.get("dataTransfer") or event.get("domTransfer") or {}
    if not isinstance(transfer, dict):
        return None
    files = transfer.get("files") or []
    if isinstance(files, dict):
        files = [files]
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("pywebviewFullPath") or file_info.get("path")
        if path:
            return str(path)
    return None


def _wait_for_dropped_path(event: Any, dnd_state: dict[str, Any] | None, *, timeout: float = 0.5) -> str | None:
    """等待 EdgeChromium 的原生文件对象到达，并取出对应完整路径。

    EdgeChromium 会把 DOM ``drop`` 回调和 ``FilesDropped`` 原生文件对象作为
    两条独立消息发送。两者的处理顺序并不稳定：若回调先到，pywebview 初次
    补路径会失败。这里按文件名短暂等待并消费暂存路径，消除该竞态条件。
    """

    if not isinstance(event, dict) or not dnd_state:
        return None
    transfer = event.get("dataTransfer") or event.get("domTransfer") or {}
    files = transfer.get("files") if isinstance(transfer, dict) else None
    if isinstance(files, dict):
        files = [files]
    names = {
        str(file_info.get("name"))
        for file_info in (files or [])
        if isinstance(file_info, dict) and file_info.get("name")
    }
    if not names:
        return None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        paths = dnd_state.get("paths")
        if isinstance(paths, list):
            for index, item in enumerate(paths):
                if not isinstance(item, (tuple, list)) or len(item) != 2:
                    continue
                file_name, full_path = (urllib.parse.unquote(str(value)) for value in item)
                if file_name in names:
                    # 路径只属于当前 drop；取走以免下一个同名文件误用。
                    paths.pop(index)
                    return full_path
        time.sleep(0.02)
    return None


def bind_dom_events(window: Any) -> None:
    """绑定 pywebview 原生拖拽事件，把完整路径转发给 JavaScript。

    pywebview 的 DOM 事件桥接在 Python 侧才能拿到桌面文件的完整路径；
    前端收到路径后继续复用统一的 ``startScan`` 流程，避免复制扫描逻辑。
    """

    try:
        from webview.dom import DOMEventHandler
    except ImportError:
        LOGGER.warning("当前 pywebview 不支持 DOM 事件，拖拽目录将不可用")
        return
    try:
        # pywebview 的内部暂存区仅在 EdgeChromium 路径竞态时作为回退使用。
        from webview.dom import _dnd_state
    except ImportError:
        _dnd_state = None

    def prevent_drag_default(_event: dict[str, Any]) -> None:
        """占位处理器：配合 DOMEventHandler 允许桌面目录落入窗口。"""

    def on_drop(event: dict[str, Any]) -> None:
        """读取 pywebviewFullPath，并安全地调用前端全局拖拽入口。"""

        path = _extract_dropped_path(event) or _wait_for_dropped_path(event, _dnd_state)
        if not path:
            LOGGER.warning("拖拽事件未提供本地完整路径，已忽略本次拖拽")
            # 发布版默认没有控制台；把失败反馈给页面，避免用户看到静默无响应。
            try:
                window.evaluate_js("window.djiColorDeskHandleDrop(null);")
            except Exception as exc:
                LOGGER.exception("通知前端拖拽失败原因时出错：%s", exc)
            return
        LOGGER.info("收到拖入路径：%s", path)
        script = f"window.djiColorDeskHandleDrop({json.dumps(path, ensure_ascii=False)});"
        try:
            window.evaluate_js(script)
        except Exception as exc:
            LOGGER.exception("通知前端处理拖入路径失败：%s", exc)

    # pywebview 官方示例要求同时拦截 dragenter、dragstart 和 dragover；缺少
    # dragstart 时，部分 Windows WebView2 环境不会把外部目录继续派发为 drop。
    # dragover 不读取文件，只负责让操作系统允许 drop 事件继续产生。
    window.dom.document.events.dragenter += DOMEventHandler(prevent_drag_default, True, True)
    window.dom.document.events.dragstart += DOMEventHandler(prevent_drag_default, True, True)
    window.dom.document.events.dragover += DOMEventHandler(prevent_drag_default, True, True, debounce=500)
    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


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
        webview.start(bind_dom_events, window, debug=debug)
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
