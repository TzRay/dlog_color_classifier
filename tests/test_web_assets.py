"""Web 页面资源的静态验收测试。"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_prototype_loads_runtime_script_and_hlg_controls() -> None:
    """页面必须加载生产脚本，并保留 HLG HDR 统计与筛选入口。"""

    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    assert '<script src="app.js"></script>' in html
    assert 'data-filter="rec2100_hlg"' in html
    assert 'id="cancelTask"' in html


def test_prototype_uses_pywebview_drop_bridge() -> None:
    """拖拽目录必须走 pywebview 的完整路径桥接，而不是 File.path。"""

    script = (ROOT / "prototype" / "app.js").read_text(encoding="utf-8")
    assert "djiColorDeskHandleDrop" in script
    assert "普通浏览器无法提供本地目录路径" in script
    drop_handler = script.split('document.addEventListener("drop"', maxsplit=1)[1]
    assert 'callApi("choose_directory")' not in drop_handler


def test_runtime_script_only_references_existing_ids() -> None:
    """防止修改原型 DOM 后，生产脚本静默操作不存在的节点。"""

    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "prototype" / "app.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([A-Za-z][A-Za-z0-9_-]*)"', html))
    referenced_ids = set(re.findall(r'\$\("#([A-Za-z][A-Za-z0-9_-]*)"\)', script))
    assert referenced_ids <= html_ids
