"""Web 单页资源的静态验收测试。"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_page_loads_runtime_script_without_prototype_snapshot() -> None:
    """生产页面只加载真实脚本，不应保留不可执行的原型演示代码。"""

    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    assert '<script src="app.js"></script>' in html
    assert "if (false)" not in html


def test_web_page_removes_workflow_history_and_undo_ui() -> None:
    """Web 页面不再暴露预演、确认、操作记录或撤销入口。"""

    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    for removed in ("整理预演", "确认执行", "confirmModal", "loadManifest", "undoLast", "data-nav"):
        assert removed not in html
    assert 'id="executeOrganize"' in html
    assert 'id="outcomePanel"' in html


def test_runtime_script_uses_direct_organize_api_and_drop_bridge() -> None:
    """前端必须调用直接整理接口，并继续支持 pywebview 拖拽目录桥接。"""

    script = (ROOT / "prototype" / "app.js").read_text(encoding="utf-8")
    assert 'callApi("execute_organize"' in script
    assert "djiColorDeskHandleDrop" in script
    assert "dropped.pywebviewFullPath || dropped.path" in script
    for removed in ("build_plan", "execute_plan", "load_manifest", "execute_undo", "preview_undo"):
        assert removed not in script


def test_task_terminal_refreshes_controls_after_state_callback() -> None:
    """扫描终态必须先写入 scanId，再解除“执行整理”的禁用状态。"""

    script = (ROOT / "prototype" / "app.js").read_text(encoding="utf-8")
    terminal = script.split('if (task.state === "failed")', maxsplit=1)[1].split("async function startScan", maxsplit=1)[0]
    assert terminal.index("onTerminal(") < terminal.index("refreshControls()")


def test_runtime_script_only_references_existing_ids() -> None:
    """防止页面调整后生产脚本静默操作不存在的 DOM 节点。"""

    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "prototype" / "app.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([A-Za-z][A-Za-z0-9_-]*)"', html))
    referenced_ids = set(re.findall(r'\$\("#([A-Za-z][A-Za-z0-9_-]*)"\)', script))
    assert referenced_ids <= html_ids


def test_web_packaging_collects_dynamic_pywebview_modules() -> None:
    """发布包必须包含 pywebview 的 DOM 与当前平台后端。"""

    spec = (ROOT / "packaging" / "dji-color-web.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("webview")' in spec
