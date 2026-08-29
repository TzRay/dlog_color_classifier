"""pywebview 桌面壳的拖拽路径回归测试。"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from dji_color_classifier.web_app import DesktopBridge, _extract_dropped_path, bind_dom_events


class _FakeEventHook:
    """记录 DOM 事件处理器的简易测试替身。"""

    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):  # noqa: ANN001
        self.handlers.append(handler)
        return self


class _FakeWindow:
    """提供 bind_dom_events 所需最小窗口接口。"""

    def __init__(self) -> None:
        events = SimpleNamespace(
            dragenter=_FakeEventHook(),
            dragstart=_FakeEventHook(),
            dragover=_FakeEventHook(),
            drop=_FakeEventHook(),
        )
        self.dom = SimpleNamespace(document=SimpleNamespace(events=events))
        self.evaluated_scripts = []

    def evaluate_js(self, script: str) -> None:
        """记录被转发给前端的脚本。"""

        self.evaluated_scripts.append(script)


class _FakeDomEventHandler:
    """模拟 pywebview DOMEventHandler 并保留回调。"""

    def __init__(self, callback, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.callback = callback


class _FakeService:
    """验证桥接方法转发的最小服务替身。"""

    def execute_organize(self, options):  # noqa: ANN001
        """记录直接整理入参。"""

        return {"received": options}


def test_extract_dropped_path_from_pywebview_event() -> None:
    """优先读取 pywebview 提供的完整桌面路径。"""

    event = {"dataTransfer": {"files": [{"name": "素材", "pywebviewFullPath": r"D:\\素材"}]}}
    assert _extract_dropped_path(event) == r"D:\\素材"


def test_extract_dropped_path_supports_legacy_transfer_name_and_fallback() -> None:
    """兼容历史 domTransfer 字段，并保留测试环境的 path 回退。"""

    assert _extract_dropped_path({"domTransfer": {"files": [{"path": "/tmp/media"}]}}) == "/tmp/media"
    assert _extract_dropped_path({"dataTransfer": {"files": []}}) is None
    assert _extract_dropped_path(None) is None


def test_bind_dom_events_forwards_full_path_to_frontend(monkeypatch) -> None:  # noqa: ANN001
    """drop 事件应把 pywebviewFullPath 安全转发到统一前端入口。"""

    webview_module = ModuleType("webview")
    dom_module = ModuleType("webview.dom")
    dom_module.DOMEventHandler = _FakeDomEventHandler
    webview_module.dom = dom_module
    monkeypatch.setitem(sys.modules, "webview", webview_module)
    monkeypatch.setitem(sys.modules, "webview.dom", dom_module)

    window = _FakeWindow()
    bind_dom_events(window)

    assert len(window.dom.document.events.dragstart.handlers) == 1
    assert len(window.dom.document.events.drop.handlers) == 1
    handler = window.dom.document.events.drop.handlers[0]
    handler.callback({"dataTransfer": {"files": [{"pywebviewFullPath": r"D:\素材"}]}})
    assert len(window.evaluated_scripts) == 1
    assert "djiColorDeskHandleDrop" in window.evaluated_scripts[0]
    assert "D:" in window.evaluated_scripts[0]


def test_desktop_bridge_exposes_direct_organize_only() -> None:
    """桌面桥接应暴露直接整理接口，不再暴露 Web 撤销流程。"""

    bridge = DesktopBridge(_FakeService())
    payload = {"scan_id": "scan_1", "mode": "copy"}
    assert bridge.execute_organize(payload) == {"received": payload}
    assert not hasattr(bridge, "build_plan")
    assert not hasattr(bridge, "execute_plan")
    assert not hasattr(bridge, "execute_undo")
