"""正式版版本号与发布产物配置的回归测试。"""

from __future__ import annotations

import re
from pathlib import Path

from dji_color_classifier import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_formal_release_version_is_consistent() -> None:
    """运行时版本与包元数据必须同时指向 2.0.0 正式版。"""

    project_file = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([^"]+)"$', project_file, flags=re.MULTILINE)

    assert version_match is not None
    assert version_match.group(1) == "2.0.0"
    assert __version__ == "2.0.0"


def test_release_workflows_only_build_web_desktop_apps() -> None:
    """普通构建与正式发布不得重新加入 CLI 或 Qt GUI 安装包。"""

    build_workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    workflows = build_workflow + release_workflow

    assert "packaging/dji-color-cli.spec" not in workflows
    assert "packaging/dji-color-gui.spec" not in workflows
    assert "dji-color-cli-" not in workflows
    assert "dji-color-gui-" not in workflows
    assert "dji-color-web-windows-x64" in build_workflow
    assert "dji-color-web-macos-arm64" in build_workflow
    assert "dji-color-web-windows-x64.zip" in release_workflow
    assert "dji-color-web-macos-arm64.zip" in release_workflow
