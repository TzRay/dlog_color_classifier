"""执行文件整理计划。"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import BinaryIO

from dji_color_classifier.core.models import ExecutionRecord, PlanAction, PlanItem


LOGGER = logging.getLogger(__name__)
COPY_BUFFER_SIZE = 4 * 1024 * 1024


class _ExecutionCancelled(Exception):
    """文件尚未移动或副本尚未发布时取消；当前项不计入完成结果。"""


def execute_plan(
    plan: list[PlanItem],
    *,
    apply: bool = False,
    on_progress: Callable[[int, int, PlanItem], None] | None = None,
    cancel_event: Event | None = None,
) -> list[ExecutionRecord]:
    """执行或预演整理计划。

    逐项返回结果；复制可在数据块之间取消，未发布的当前项不会计入结果。
    已成功发布的文件即使随后收到取消信号，也会保留成功记录。
    """

    records: list[ExecutionRecord] = []
    total = len(plan)
    for completed, item in enumerate(plan, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        if item.blocked:
            records.append(
                ExecutionRecord(
                    source=item.source,
                    target=item.target,
                    action=item.action,
                    mode=item.scan_result.mode,
                    success=False,
                    message=item.reason or "计划存在冲突，未执行",
                )
            )
            if on_progress is not None:
                on_progress(completed, total, item)
            continue
        if item.skipped or item.action is PlanAction.NONE or item.target is None:
            records.append(
                ExecutionRecord(
                    source=item.source,
                    target=item.target,
                    action=item.action,
                    mode=item.scan_result.mode,
                    success=True,
                    message=item.reason or "无需处理",
                )
            )
            if on_progress is not None:
                on_progress(completed, total, item)
            continue

        if not apply:
            records.append(
                ExecutionRecord(
                    source=item.source,
                    target=item.target,
                    action=item.action,
                    mode=item.scan_result.mode,
                    success=True,
                    message="预演模式，未修改文件",
                )
            )
            if on_progress is not None:
                on_progress(completed, total, item)
            continue

        record = _execute_item(item, cancel_event=cancel_event)
        if record is None:
            break
        records.append(record)
        if on_progress is not None:
            on_progress(completed, total, item)
    return records


def _execute_item(item: PlanItem, *, cancel_event: Event | None = None) -> ExecutionRecord | None:
    """执行单个计划项；复制中途取消时返回空值，由外层结束任务。"""

    assert item.target is not None
    try:
        if item.action is PlanAction.DELETE:
            if not item.source.exists():
                raise FileNotFoundError(f"待删除文件不存在：{item.source}")
            if item.scan_result.size and item.source.stat().st_size != item.scan_result.size:
                raise RuntimeError(f"待删除文件大小与 manifest 不一致，已停止：{item.source}")
            item.source.unlink()
            return ExecutionRecord(
                item.source,
                item.target,
                item.action,
                item.scan_result.mode,
                True,
                "删除成功",
                source_size=item.scan_result.size,
                target_size=None,
            )

        source_size = item.source.stat().st_size if item.source.exists() else None
        item.target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(item.target):
            raise FileExistsError(f"目标文件已存在：{item.target}")

        if item.action is PlanAction.RENAME or item.action is PlanAction.MOVE:
            _check_cancelled(cancel_event)
            _move_safely(item.source, item.target)
        elif item.action is PlanAction.COPY:
            source_size = _copy_safely(item.source, item.target, cancel_event)
        else:
            return ExecutionRecord(item.source, item.target, item.action, item.scan_result.mode, True, "无需处理")

        target_size = item.target.stat().st_size if item.target.exists() else None
        return ExecutionRecord(
            item.source,
            item.target,
            item.action,
            item.scan_result.mode,
            True,
            "执行成功",
            source_size=source_size,
            target_size=target_size,
        )
    except _ExecutionCancelled:
        return None
    except Exception as exc:
        return ExecutionRecord(item.source, item.target, item.action, item.scan_result.mode, False, str(exc))


def _check_cancelled(cancel_event: Event | None) -> None:
    """在每个数据块以及移动、发布之前响应取消。"""

    if cancel_event is not None and cancel_event.is_set():
        raise _ExecutionCancelled


def _copy_stream(source: BinaryIO, destination: BinaryIO, cancel_event: Event | None) -> int:
    """使用固定大小缓冲区复制数据，并返回实际写入的字节数。"""

    copied_size = 0
    while True:
        _check_cancelled(cancel_event)
        chunk = source.read(COPY_BUFFER_SIZE)
        if not chunk:
            return copied_size
        written = destination.write(chunk)
        if written != len(chunk):
            raise OSError("复制未完整写入当前数据块")
        copied_size += written


def _copy_safely(source: Path, target: Path, cancel_event: Event | None) -> int:
    """复制到同目录临时文件，校验后一次性发布，任何失败均清理临时文件。"""

    _check_cancelled(cancel_event)
    temporary: Path | None = None
    try:
        with source.open("rb") as input_file:
            before = os.fstat(input_file.fileno())
            # 使用目标目录保证最终发布不跨文件系统，短临时名也避免路径长度膨胀。
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".dji-copy-", suffix=".tmp", dir=target.parent, delete=False
            ) as output_file:
                temporary = Path(output_file.name)
                copied_size = _copy_stream(input_file, output_file, cancel_event)
                _check_cancelled(cancel_event)
                output_file.flush()
                os.fsync(output_file.fileno())
                after = os.fstat(input_file.fileno())
                if (
                    copied_size != before.st_size
                    or os.fstat(output_file.fileno()).st_size != before.st_size
                    or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
                ):
                    raise OSError("复制校验失败：源文件发生变化或目标文件不完整")

        shutil.copystat(source, temporary)
        _check_cancelled(cancel_event)
        _publish_copy(temporary, target)
        # 发布完成后不再检查取消：此文件已经存在，必须如实计入成功结果。
        return copied_size
    finally:
        if temporary is not None:
            _remove_copy_temporary(temporary)


def _remove_copy_temporary(path: Path) -> None:
    """仅清理本次复制创建的临时文件，并兼容 Windows 只读文件属性。"""

    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        try:
            if os.name == "nt":
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
            path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("复制临时文件清理失败：%s，原因：%s", path, exc)
    except OSError as exc:
        LOGGER.warning("复制临时文件清理失败：%s，原因：%s", path, exc)


def _publish_copy(temporary: Path, target: Path) -> None:
    """原子发布完整副本；即使检查后出现同名目标，也绝不覆盖。"""

    if os.name == "nt":
        # Windows 的 rename 自身拒绝覆盖，适用于 NTFS、FAT 和 exFAT。
        os.rename(temporary, target)
        return
    if _rename_exclusive_posix(temporary, target):
        return
    # 旧系统没有排他 rename 时，以硬链接原子占用目标名；不支持时安全失败。
    # 临时目录项由调用方 finally 清理，清理异常不会把已发布副本误报为失败。
    os.link(temporary, target)


def _move_safely(source: Path, target: Path) -> None:
    """移动或改名同样拒绝覆盖；缺少排他移动能力时保留源文件并明确失败。"""

    if os.name == "nt":
        os.rename(source, target)
    elif not _rename_exclusive_posix(source, target):
        # 移动不采用“硬链接再删源文件”，避免删除失败产生部分完成状态。
        raise OSError(errno.ENOTSUP, "当前文件系统不支持安全的排他移动，源文件未修改", str(target))


def _rename_exclusive_posix(source: Path, target: Path) -> bool:
    """调用 Linux/macOS 的排他重命名；接口不可用时允许硬链接后备方案。"""

    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        if rename is None:
            return False
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # AT_FDCWD=-100，RENAME_NOREPLACE=1；内核对已存在目标返回 EEXIST。
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            return False
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # macOS 的 RENAME_EXCL=4，同样适用于不支持硬链接的外置存储。
        result = rename(os.fsencode(source), os.fsencode(target), 4)
    else:
        return False
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        return False
    raise OSError(error, f"无法安全发布目标文件：{os.strerror(error)}", str(target))


def build_undo_plan(records: list[ExecutionRecord]) -> list[PlanItem]:
    """根据执行记录生成撤销计划。"""

    from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult

    plan: list[PlanItem] = []
    for record in records:
        if not record.success or record.target is None:
            continue
        dummy_result = ScanResult(
            path=record.target,
            mode=record.mode if record.mode is not ColorMode.ERROR else ColorMode.UNKNOWN,
            evidence=ClassificationEvidence(None, None, detail="undo"),
            size=record.target_size or 0,
        )
        if record.action in {PlanAction.RENAME, PlanAction.MOVE}:
            plan.append(PlanItem(record.target, record.source, PlanAction.MOVE, dummy_result))
        elif record.action is PlanAction.COPY:
            plan.append(PlanItem(record.target, record.target, PlanAction.DELETE, dummy_result))
    return plan
