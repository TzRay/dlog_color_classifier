"""批量扫描的异常隔离、目录枚举与取消回归测试。"""

from pathlib import Path
from threading import Event

import pytest

from dji_color_classifier.core import scanner
from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult


def test_scan_continues_after_unexpected_file_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未知解析异常只影响当前文件，不丢失已经得到和后续的结果。"""

    for name in ("01_good.mp4", "02_bad.mp4", "03_good.mp4"):
        (tmp_path / name).write_bytes(b"video")

    def classify(path: Path) -> ScanResult:
        if "bad" in path.name:
            raise ValueError("测试损坏字段")
        return ScanResult(path, ColorMode.DLOG, ClassificationEvidence(2, None))

    monkeypatch.setattr(scanner, "classify_file", classify)
    results = scanner.scan_directory(tmp_path)

    assert [result.mode for result in results] == [ColorMode.DLOG, ColorMode.ERROR, ColorMode.DLOG]
    assert "测试损坏字段" in results[1].error


def test_scan_enumerates_once_and_reports_initial_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """总数来自同一轮目录枚举，首个文件读取前即可显示进度。"""

    source = tmp_path / "video.MP4"
    source.write_bytes(b"video")
    calls = []
    enumerate_files = scanner.iter_video_files

    def counted(directory, **kwargs):
        calls.append(directory)
        return enumerate_files(directory, **kwargs)

    monkeypatch.setattr(scanner, "iter_video_files", counted)
    monkeypatch.setattr(scanner, "classify_file", lambda path: ScanResult(
        path, ColorMode.DLOG, ClassificationEvidence(2, None)
    ))
    progress = []
    results = scanner.scan_directory(tmp_path, on_progress=lambda *args: progress.append(args))

    assert len(results) == 1
    assert calls == [tmp_path]
    assert progress == [(0, 1, tmp_path), (1, 1, source)]


def test_cancelled_enumeration_does_not_open_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """排队中取消后，不应再访问磁盘或网络目录。"""

    cancelled = Event()
    cancelled.set()

    def unexpected_scandir(_directory):
        raise AssertionError("已取消的任务不应读取目录")

    monkeypatch.setattr(scanner.os, "scandir", unexpected_scandir)
    assert scanner.scan_directory(tmp_path, recursive=True, cancel_event=cancelled) == []


def test_cancel_during_enumeration_stops_before_classification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """大量目录项枚举期间也会检查取消信号。"""

    for index in range(4):
        (tmp_path / f"video_{index}.mp4").write_bytes(b"video")
    cancelled = Event()
    real_scandir = scanner.os.scandir
    visited = []

    class Entries:
        def __enter__(self):
            self.entries = real_scandir(tmp_path)
            return self

        def __exit__(self, *_args):
            self.entries.close()

        def __iter__(self):
            for entry in self.entries:
                visited.append(entry.name)
                if len(visited) == 2:
                    cancelled.set()
                yield entry

    def unexpected_classification(_path: Path) -> ScanResult:
        raise AssertionError("枚举期间已取消，不应继续分类")

    monkeypatch.setattr(scanner.os, "scandir", lambda _directory: Entries())
    monkeypatch.setattr(scanner, "classify_file", unexpected_classification)
    assert scanner.scan_directory(tmp_path, cancel_event=cancelled) == []
    assert len(visited) == 2


def test_recursive_scan_preserves_suffix_and_scope(tmp_path: Path) -> None:
    """扩展名大小写不敏感，非递归扫描不包含子目录中的视频。"""

    nested = tmp_path / "子目录"
    nested.mkdir()
    first = tmp_path / "first.MOV"
    second = nested / "second.m4v"
    first.touch()
    second.touch()
    (tmp_path / "note.txt").touch()

    assert scanner.iter_video_files(tmp_path) == [first]
    assert set(scanner.iter_video_files(tmp_path, recursive=True)) == {first, second}
