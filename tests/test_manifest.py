"""manifest 测试。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import ColorMode, ExecutionRecord, PlanAction


def test_manifest_roundtrip(tmp_path: Path) -> None:
    """manifest 应能写入并读回执行记录。"""

    record = ExecutionRecord(
        source=tmp_path / "a.mp4",
        target=tmp_path / "dlog" / "a.mp4",
        action=PlanAction.MOVE,
        mode=ColorMode.DLOG,
        success=True,
        message="执行成功",
    )
    manifest = create_manifest(tmp_path, "move", [record])
    path = write_manifest(manifest, tmp_path / "manifest.json")

    loaded = read_manifest(path)

    assert loaded.operation == "move"
    assert loaded.records[0].target == record.target
