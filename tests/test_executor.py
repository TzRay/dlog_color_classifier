"""执行与撤销安全测试。"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from dji_color_classifier.core import executor
from dji_color_classifier.core.executor import build_undo_plan, execute_plan
from dji_color_classifier.core.models import (
    ClassificationEvidence,
    ColorMode,
    ExecutionRecord,
    PlanAction,
    PlanItem,
    ScanResult,
)


def copy_item(tmp_path: Path, name: str = "source.mp4", content: bytes = b"complete video") -> PlanItem:
    """构造真实源文件的复制计划，目标位于同一测试目录。"""

    source = tmp_path / name
    source.write_bytes(content)
    result = ScanResult(source, ColorMode.DLOG, ClassificationEvidence(None, None), size=len(content))
    return PlanItem(source, tmp_path / "output" / name, PlanAction.COPY, result)


def test_copy_publishes_complete_file_and_preserves_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """最终名称仅在内容完整之后出现，源内容和时间戳均保持不变。"""

    content = b"video" * (executor.COPY_BUFFER_SIZE // 5 + 1)
    item = copy_item(tmp_path, content=content)
    os.utime(item.source, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    original_publish = executor._publish_copy

    def inspect_publish(temporary: Path, target: Path) -> None:
        assert temporary.parent == target.parent
        assert temporary.read_bytes() == content
        assert not target.exists()
        original_publish(temporary, target)

    monkeypatch.setattr(executor, "_publish_copy", inspect_publish)
    records = execute_plan([item], apply=True)

    assert records[0].success
    assert records[0].source_size == records[0].target_size == len(content)
    assert item.source.read_bytes() == item.target.read_bytes() == content
    assert item.source.stat().st_mtime_ns == item.target.stat().st_mtime_ns
    assert list(item.target.parent.iterdir()) == [item.target]


def test_copy_failure_removes_partial_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟磁盘写满后只报告失败，不留下最终文件或半成品临时文件。"""

    item = copy_item(tmp_path)

    def fail_during_write(source, destination, cancel_event):
        destination.write(b"partial")
        raise OSError("模拟磁盘空间不足")

    monkeypatch.setattr(executor, "_copy_stream", fail_during_write)
    records = execute_plan([item], apply=True)

    assert not records[0].success
    assert "磁盘空间不足" in records[0].message
    assert item.source.read_bytes() == b"complete video"
    assert not item.target.exists()
    assert not list(item.target.parent.iterdir())


def test_copy_rejects_source_changed_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """复制期间源文件被修改时拒绝发布，避免把不一致副本标记为完成。"""

    item = copy_item(tmp_path)
    original_stream = executor._copy_stream

    def change_after_read(source, destination, cancel_event):
        copied = original_stream(source, destination, cancel_event)
        before = item.source.stat()
        os.utime(item.source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        return copied

    monkeypatch.setattr(executor, "_copy_stream", change_after_read)
    records = execute_plan([item], apply=True)

    assert not records[0].success
    assert "复制校验失败" in records[0].message
    assert not list(item.target.parent.iterdir())


def test_copy_never_overwrites_target_created_during_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """在发布前插入竞争写入，验证最终的系统操作仍拒绝覆盖。"""

    item = copy_item(tmp_path)
    original_publish = executor._publish_copy

    def publish_after_competing_writer(temporary: Path, target: Path) -> None:
        target.write_bytes(b"other process data")
        original_publish(temporary, target)

    monkeypatch.setattr(executor, "_publish_copy", publish_after_competing_writer)
    records = execute_plan([item], apply=True)

    assert not records[0].success
    assert item.target.read_bytes() == b"other process data"
    assert list(item.target.parent.iterdir()) == [item.target]


@pytest.mark.parametrize("action", [PlanAction.MOVE, PlanAction.RENAME])
def test_move_never_overwrites_target_created_after_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: PlanAction
) -> None:
    """移动和改名遇到检查后出现的目标时，必须保留原文件与竞争目标。"""

    copied = copy_item(tmp_path)
    item = PlanItem(copied.source, copied.target, action, copied.scan_result)
    original_move = executor._move_safely

    def move_after_competing_writer(source: Path, target: Path) -> None:
        target.write_bytes(b"other process data")
        original_move(source, target)

    monkeypatch.setattr(executor, "_move_safely", move_after_competing_writer)
    records = execute_plan([item], apply=True)

    assert not records[0].success
    assert item.target.read_bytes() == b"other process data"
    assert item.source.read_bytes() == b"complete video"


def test_cancel_mid_copy_keeps_only_completed_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """复制中途取消会清理当前副本，同时保留上一文件的成功结果。"""

    first = copy_item(tmp_path, "first.mp4")
    second = copy_item(tmp_path, "second.mp4")
    cancel_event = Event()
    original_stream = executor._copy_stream
    progress = []
    monkeypatch.setattr(executor, "COPY_BUFFER_SIZE", 4)

    class CancelAfterRead:
        """在真实复制循环读完第一个块时触发取消。"""

        def __init__(self, wrapped):
            self.wrapped = wrapped

        def read(self, size):
            chunk = self.wrapped.read(size)
            cancel_event.set()
            return chunk

    def cancel_second_file(source, destination, event):
        if Path(source.name) == second.source:
            return original_stream(CancelAfterRead(source), destination, event)
        return original_stream(source, destination, event)

    monkeypatch.setattr(executor, "_copy_stream", cancel_second_file)
    records = execute_plan(
        [first, second], apply=True, cancel_event=cancel_event,
        on_progress=lambda completed, total, item: progress.append(completed),
    )

    assert len(records) == 1 and records[0].success
    assert progress == [1]
    assert first.target.read_bytes() == first.source.read_bytes()
    assert not second.target.exists()
    assert list(first.target.parent.iterdir()) == [first.target]


def test_cancel_after_publish_retains_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消与发布交错时，已经落地的完整文件仍须如实计入成功。"""

    item = copy_item(tmp_path)
    cancel_event = Event()
    original_publish = executor._publish_copy

    def publish_then_cancel(temporary: Path, target: Path) -> None:
        original_publish(temporary, target)
        cancel_event.set()

    monkeypatch.setattr(executor, "_publish_copy", publish_then_cancel)
    records = execute_plan([item], apply=True, cancel_event=cancel_event)

    assert len(records) == 1 and records[0].success
    assert item.target.read_bytes() == item.source.read_bytes()


def test_cancel_cleans_readonly_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """临时副本已继承只读属性后取消，Windows 下也能清理干净。"""

    item = copy_item(tmp_path)
    cancel_event = Event()
    original_copystat = executor.shutil.copystat

    def copy_readonly_attributes(source: Path, target: Path) -> None:
        original_copystat(source, target)
        target.chmod(stat.S_IREAD)
        cancel_event.set()

    monkeypatch.setattr(executor.shutil, "copystat", copy_readonly_attributes)
    records = execute_plan([item], apply=True, cancel_event=cancel_event)

    assert records == []
    assert not list(item.target.parent.iterdir())
    assert item.source.read_bytes() == b"complete video"


def test_copy_accepts_empty_source(tmp_path: Path) -> None:
    """零字节文件也是完整副本，校验不能把合法空文件误判为失败。"""

    item = copy_item(tmp_path, content=b"")

    records = execute_plan([item], apply=True)

    assert records[0].success
    assert records[0].source_size == records[0].target_size == 0
    assert item.target.read_bytes() == b""


@pytest.mark.parametrize(("platform", "symbol", "flag"), [("linux", "renameat2", 1), ("darwin", "renamex_np", 4)])
def test_posix_exclusive_rename_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, symbol: str, flag: int
) -> None:
    """验证两个平台传入排他标志，并保留系统返回的目标冲突错误。"""

    temporary = tmp_path / "ready.tmp"
    target = tmp_path / "target.mp4"
    temporary.write_bytes(b"complete")
    target.write_bytes(b"existing")

    def native_rename(*args):
        assert args[-1] == flag
        if target.exists():
            ctypes.set_errno(errno.EEXIST)
            return -1
        os.link(temporary, target)
        temporary.unlink()
        return 0

    monkeypatch.setattr(executor.sys, "platform", platform)
    monkeypatch.setattr(executor.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(**{symbol: native_rename}))

    with pytest.raises(FileExistsError):
        executor._rename_exclusive_posix(temporary, target)
    assert target.read_bytes() == b"existing"
    assert temporary.read_bytes() == b"complete"

    target.unlink()
    assert executor._rename_exclusive_posix(temporary, target)
    assert target.read_bytes() == b"complete"
    assert not temporary.exists()


def test_publish_hardlink_fallback_preserves_existing_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧系统使用硬链接后备时同样拒绝覆盖，成功后副本内容立即完整。"""

    temporary = tmp_path / "ready.tmp"
    target = tmp_path / "target.mp4"
    temporary.write_bytes(b"complete")
    target.write_bytes(b"existing")
    monkeypatch.setattr(executor, "os", SimpleNamespace(name="posix", link=os.link))
    monkeypatch.setattr(executor, "_rename_exclusive_posix", lambda *args: False)

    with pytest.raises(FileExistsError):
        executor._publish_copy(temporary, target)
    assert target.read_bytes() == b"existing"
    assert temporary.read_bytes() == b"complete"

    target.unlink()
    executor._publish_copy(temporary, target)
    assert target.read_bytes() == b"complete"


def test_blocked_item_never_executes(tmp_path: Path) -> None:
    """计划中的整组冲突直接形成失败记录，不执行其中目标空闲的文件。"""

    item = copy_item(tmp_path)
    blocked = PlanItem(item.source, item.target, item.action, item.scan_result, blocked=True, reason="整组冲突")

    records = execute_plan([blocked], apply=True)

    assert not records[0].success
    assert records[0].message == "整组冲突"
    assert not item.target.exists()


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
