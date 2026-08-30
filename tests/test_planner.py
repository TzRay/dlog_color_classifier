"""整理计划测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ConflictPolicy, PlanAction, ScanResult
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


def test_hlg_uses_directory_and_prefix(tmp_path: Path) -> None:
    """HLG 应归入独立目录，并在前缀模式使用 hlg_。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    hlg_result = result(source, ColorMode.REC2100_HLG)

    move_plan = build_plan([hlg_result], root=tmp_path, mode="move")
    prefix_plan = build_plan([hlg_result], root=tmp_path, mode="prefix")

    assert move_plan[0].target == tmp_path / "hlg" / "DJI_0001.MP4"
    assert prefix_plan[0].target == tmp_path / "hlg_DJI_0001.MP4"


def test_conflicting_metadata_is_not_automatically_organized(tmp_path: Path) -> None:
    """存在可靠元数据冲突时，移动和复制都必须跳过该文件。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"")
    conflict_result = ScanResult(
        source,
        ColorMode.UNKNOWN,
        ClassificationEvidence(None, None, primary_source="conflict"),
    )

    plan = build_plan([conflict_result], root=tmp_path, mode="copy")

    assert plan[0].skipped
    assert plan[0].target is None
    assert plan[0].reason == "元数据证据冲突，禁止自动整理"


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


@pytest.mark.parametrize(
    ("policy", "expected_skipped", "expected_name"),
    [
        (ConflictPolicy.ERROR, True, "DJI_0001.srt"),
        (ConflictPolicy.SKIP, True, "DJI_0001.srt"),
        (ConflictPolicy.SUFFIX, False, "DJI_0001_001.srt"),
    ],
)
def test_sidecar_conflict_follows_selected_policy(
    tmp_path: Path,
    policy: ConflictPolicy,
    expected_skipped: bool,
    expected_name: str,
) -> None:
    """伴随文件必须与视频使用相同的冲突策略。"""

    source = tmp_path / "DJI_0001.MP4"
    sidecar = tmp_path / "DJI_0001.srt"
    source.write_bytes(b"video")
    sidecar.write_text("subtitle", encoding="utf-8")
    target_dir = tmp_path / "dlog"
    target_dir.mkdir()
    (target_dir / sidecar.name).write_text("existing", encoding="utf-8")

    plan = build_plan(
        [result(source, ColorMode.DLOG)],
        root=tmp_path,
        mode="copy",
        conflict_policy=policy,
        with_sidecars=True,
    )

    assert plan[1].source == sidecar
    assert plan[1].skipped is expected_skipped
    assert plan[1].target is not None
    assert plan[1].target.name == expected_name
