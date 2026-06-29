"""执行与撤销安全测试。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.executor import build_undo_plan, execute_plan
from dji_color_classifier.core.models import ColorMode, ExecutionRecord, PlanAction


def test_copy_undo_refuses_changed_target(tmp_path: Path) -> None:
    """复制撤销前必须校验目标文件大小，避免误删用户改过的文件。"""

    copied = tmp_path / "copied.mp4"
    copied.write_bytes(b"changed")
    record = ExecutionRecord(
        source=tmp_path / "source.mp4",
        target=copied,
        action=PlanAction.COPY,
        mode=ColorMode.DLOG,
        success=True,
        source_size=4,
        target_size=4,
    )

    undo_plan = build_undo_plan([record])
    results = execute_plan(undo_plan, apply=True)

    assert copied.exists()
    assert not results[0].success
    assert "大小与 manifest 不一致" in results[0].message
