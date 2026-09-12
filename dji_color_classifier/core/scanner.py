"""视频扫描流程。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from collections.abc import Callable
from threading import Event
from typing import Iterable

from dji_color_classifier.core.classifier import classify_file
from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
LOGGER = logging.getLogger(__name__)


def iter_video_files(
    directory: Path, *, recursive: bool = False, cancel_event: Event | None = None
) -> list[Path]:
    """可取消地枚举视频，复用目录项属性并避免递归进入目录链接。"""

    pending = [directory]
    videos: dict[Path, Path] = {}
    while pending:
        if cancel_event is not None and cancel_event.is_set():
            break
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if cancel_event is not None and cancel_event.is_set():
                    break
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not path.is_symlink():
                        pending.append(path)
                elif path.suffix.lower() in VIDEO_SUFFIXES and entry.is_file():
                    videos[path.resolve()] = path
    return sorted(videos.values(), key=lambda item: str(item).lower())


def scan_directory(
    directory: Path,
    *,
    recursive: bool = False,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cancel_event: Event | None = None,
) -> list[ScanResult]:
    """扫描目录并返回每个视频的识别结果。

    ``on_progress`` 与 ``cancel_event`` 是 Web/GUI 长任务使用的可选扩展，
    不改变 CLI 和既有调用方的默认行为。扫描到单个文件时先检查取消信号，
    避免用户在批量识别期间关闭窗口后仍继续读取后续大文件。
    """

    files = iter_video_files(directory, recursive=recursive, cancel_event=cancel_event)
    results: list[ScanResult] = []
    total = len(files)
    if on_progress is not None:
        on_progress(0, total, directory)
    for completed, path in enumerate(files, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            result = classify_file(path)
        except Exception as exc:
            # 单个文件或新解析规则的异常不能吞掉本批次已经完成的识别结果。
            LOGGER.exception("识别文件失败，继续处理后续素材：%s", path)
            result = ScanResult(
                path=path,
                mode=ColorMode.ERROR,
                evidence=ClassificationEvidence(None, None, detail="单文件识别异常"),
                error=f"识别失败：{type(exc).__name__}: {exc}",
            )
        results.append(result)
        if on_progress is not None:
            on_progress(completed, total, path)
    return results


def summarize_results(results: Iterable[ScanResult]) -> dict[str, int]:
    """统计各色彩模式数量。"""

    counts: dict[str, int] = {}
    for result in results:
        label = result.mode.label
        counts[label] = counts.get(label, 0) + 1
    return counts
