"""整理计划测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, PlanAction, ScanResult
from dji_color_classifier.core.planner import build_plan


def result(path: Path, mode: ColorMode) -> ScanResult:
    """构造扫描结果。"""

    return ScanResult(path=path, mode=mode, evidence=ClassificationEvidence(None, None))


def test_prefix_only_handles_log_modes(tmp_path: Path) -> None:
    """前缀模式只处理 D-Log 和 D-Log2。"""

    dlog = tmp_path / "DJI_0001.MP4"
    rec709 = tmp_path / "DJI_0002.MP4"
    dlog.write_bytes(b"")
    rec709.write_bytes(b"")

    plan = build_plan([result(dlog, ColorMode.DLOG), result(rec709, ColorMode.REC709)], root=tmp_path, mode="prefix")

    assert plan[0].action is PlanAction.RENAME
    assert plan[0].target == tmp_path / "dlog_DJI_0001.MP4"
    assert plan[1].skipped


def test_move_uses_mode_directories(tmp_path: Path) -> None:
    """移动模式应按色彩模式生成分类目录。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    plan = build_plan([result(source, ColorMode.DLOG2)], root=tmp_path, mode="move")

    assert plan[0].action is PlanAction.MOVE
    assert plan[0].target == tmp_path / "dlog2" / "DJI_0001.MP4"


def test_rejects_name_template_that_creates_a_path(tmp_path: Path) -> None:
    """文件名模板不得越权生成子目录。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")

    with pytest.raises(ValueError, match="不能包含目录"):
        build_plan([result(source, ColorMode.DLOG)], root=tmp_path, mode="prefix", name_template="bad/{original}")


def test_rejects_directory_template_that_escapes_root(tmp_path: Path) -> None:
    """目录模板不得通过上级路径跳出扫描根目录。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")

    with pytest.raises(ValueError, match="相对路径"):
        build_plan([result(source, ColorMode.DLOG)], root=tmp_path, mode="move", dir_template="../outside")
