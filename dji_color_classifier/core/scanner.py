"""视频扫描流程。"""

from __future__ import annotations

from pathlib import Path
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


def scan_directory(directory: Path, *, recursive: bool = False) -> list[ScanResult]:
    """扫描目录并返回每个视频的识别结果。"""

    return [classify_file(path) for path in iter_video_files(directory, recursive=recursive)]


def summarize_results(results: Iterable[ScanResult]) -> dict[str, int]:
    """统计各色彩模式数量。"""

    counts: dict[str, int] = {}
    for result in results:
        label = result.mode.label
        counts[label] = counts.get(label, 0) + 1
    return counts
