"""整理计划测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dji_color_classifier.core.executor import execute_plan
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
    ("policy", "expected_skipped", "expected_blocked", "expected_name"),
    [
        (ConflictPolicy.ERROR, False, True, "DJI_0001.srt"),
        (ConflictPolicy.SKIP, True, False, "DJI_0001.srt"),
        (ConflictPolicy.SUFFIX, False, False, "DJI_0001_001.srt"),
    ],
)
def test_sidecar_conflict_follows_selected_policy(
    tmp_path: Path,
    policy: ConflictPolicy,
    expected_skipped: bool,
    expected_blocked: bool,
    expected_name: str,
) -> None:
    """只有字幕目标冲突时，视频与字幕仍应整组跳过、报错或统一编号。"""

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
    assert plan[1].blocked is expected_blocked
    assert plan[1].target is not None
    assert plan[1].target.name == expected_name
    assert plan[0].skipped is expected_skipped
    assert plan[0].blocked is expected_blocked
    assert plan[0].target.stem == plan[1].target.stem

    records = execute_plan(plan, apply=True)
    assert (target_dir / sidecar.name).read_text(encoding="utf-8") == "existing"
    if policy is ConflictPolicy.ERROR:
        assert all(not record.success for record in records)
        assert not plan[0].target.exists()
    elif policy is ConflictPolicy.SKIP:
        assert not plan[0].target.exists()
    else:
        assert all(record.success for record in records)
        assert plan[0].target.read_bytes() == b"video"
        assert plan[1].target.read_text(encoding="utf-8") == "subtitle"


def test_group_suffix_skips_every_occupied_member(tmp_path: Path) -> None:
    """序号必须让整组目标均可用，不能分别为视频和伴随文件选择不同序号。"""

    source = tmp_path / "DJI_0001.MP4"
    source.write_bytes(b"video")
    (tmp_path / "DJI_0001.srt").write_bytes(b"subtitle")
    (tmp_path / "DJI_0001.LRF").write_bytes(b"proxy")
    destination = tmp_path / "dlog"
    destination.mkdir()
    for name in ("DJI_0001.MP4", "DJI_0001_001.srt", "DJI_0001_002.LRF"):
        (destination / name).write_bytes(b"existing")

    plan = build_plan(
        [result(source, ColorMode.DLOG)], root=tmp_path, mode="move",
        conflict_policy=ConflictPolicy.SUFFIX, with_sidecars=True,
    )

    assert len(plan) == 3
    assert {item.target.stem for item in plan} == {"DJI_0001_003"}
    assert {item.target.suffix for item in plan} == {".MP4", ".srt", ".LRF"}
    assert all(not item.skipped and not item.blocked for item in plan)


def test_group_suffix_reserves_targets_for_following_groups(tmp_path: Path) -> None:
    """递归扫描遇到同名视频时，本批次尚未落盘的整组目标也必须参与冲突检查。"""

    results = []
    for folder in ("camera_a", "camera_b"):
        directory = tmp_path / folder
        directory.mkdir()
        video = directory / "DJI_0001.MP4"
        video.write_bytes(b"video")
        (directory / "DJI_0001.srt").write_bytes(b"subtitle")
        results.append(result(video, ColorMode.DLOG))

    plan = build_plan(
        results, root=tmp_path, mode="copy", conflict_policy=ConflictPolicy.SUFFIX, with_sidecars=True
    )

    assert [item.target.stem for item in plan] == ["DJI_0001", "DJI_0001", "DJI_0001_001", "DJI_0001_001"]
    assert len({item.target for item in plan}) == 4


def test_sidecars_enumerate_each_source_directory_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同目录多个视频共用一次伴随文件索引，扫描次数不随视频数量增长。"""

    results = []
    source_directories = []
    for folder in ("camera_a", "camera_b"):
        directory = tmp_path / folder
        directory.mkdir()
        source_directories.append(directory)
        for index in range(3):
            video = directory / f"DJI_{index:04d}.MP4"
            video.write_bytes(b"video")
            video.with_suffix(".srt").write_bytes(b"subtitle")
            results.append(result(video, ColorMode.DLOG))

    enumerated = []
    original_iterdir = Path.iterdir

    def count_iterdir(path):
        enumerated.append(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", count_iterdir)
    plan = build_plan(
        results, root=tmp_path, mode="copy", conflict_policy=ConflictPolicy.SUFFIX, with_sidecars=True
    )

    assert len(plan) == 12
    assert enumerated == source_directories
