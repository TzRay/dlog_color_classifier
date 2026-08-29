"""视频扫描流程。"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from threading import Event
from typing import Iterable

from dji_color_classifier.core.classifier import classify_file
from dji_color_classifier.core.models import ScanResult


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


def iter_video_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    """枚举目录中的视频文件，后缀大小写不敏感。"""

    pattern = "**/*" if recursive else "*"
    videos: dict[Path, Path] = {}
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
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

    files = iter_video_files(directory, recursive=recursive)
    results: list[ScanResult] = []
    total = len(files)
    for completed, path in enumerate(files, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        results.append(classify_file(path))
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
