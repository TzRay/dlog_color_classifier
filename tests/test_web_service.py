"""Web 应用服务的直接整理测试。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
from dji_color_classifier.web_service import ApplicationService


def wait_task(service: ApplicationService, task_id: str) -> dict:
    """等待短任务结束并返回完整快照。"""

    for _ in range(200):
        snapshot = service.get_task_status(task_id)
        if snapshot["state"] in {"completed", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"任务未在预期时间内完成：{task_id}")


def fake_result(path: Path) -> ScanResult:
    """根据文件名构造轻量、可执行的识别结果。"""

    mode = {
        "DLOG": ColorMode.DLOG,
        "DLOG2": ColorMode.DLOG2,
        "HLG": ColorMode.REC2100_HLG,
        "709": ColorMode.REC709,
    }.get(path.stem.split("_")[-1], ColorMode.UNKNOWN)
    evidence = ClassificationEvidence(
        color_gamma_sxs={ColorMode.DLOG: 2, ColorMode.DLOG2: 22}.get(mode),
        record_mode=8 if mode is ColorMode.REC709 else None,
        primary_source="djmd_gamma_enum" if mode in {ColorMode.DLOG, ColorMode.DLOG2} else "djmd_record_mode",
        confidence="medium",
        detail="测试证据",
    )
    return ScanResult(path=path, mode=mode, evidence=evidence, size=path.stat().st_size)


def scan_directory(service: ApplicationService, root: Path) -> dict:
    """执行测试扫描并返回扫描 DTO。"""

    handle = service.start_scan({"directory": str(root), "recursive": True})
    snapshot = wait_task(service, handle["task_id"])
    assert snapshot["state"] == "completed", snapshot
    return snapshot["result"]


def test_web_service_scans_and_organizes_directly_without_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web 端整理不依赖计划确认，也不生成 manifest 或撤销数据。"""

    import dji_color_classifier.core.scanner as scanner

    source_dlog = tmp_path / "DJI_DLOG.MP4"
    source_unknown = tmp_path / "DJI_UNKNOWN.MP4"
    source_dlog.write_bytes(b"dlog")
    source_unknown.write_bytes(b"unknown")
    monkeypatch.setattr(scanner, "classify_file", fake_result)

    service = ApplicationService(max_workers=1)
    try:
        scan = scan_directory(service, tmp_path)
        assert not hasattr(service, "build_plan")
        assert not hasattr(service, "execute_plan")

        handle = service.execute_organize(
            {
                "scan_id": scan["scan_id"],
                "mode": "copy",
                "conflict_policy": "suffix",
                "with_sidecars": False,
            }
        )
        snapshot = wait_task(service, handle["task_id"])
        assert snapshot["state"] == "completed", snapshot
        result = snapshot["result"]
        assert result["success_count"] == 1
        assert result["skipped_count"] == 1
        assert result["failed_count"] == 0
        assert "manifest_path" not in result
        assert (tmp_path / "dlog" / source_dlog.name).is_file()
        assert source_unknown.is_file()
        assert not (tmp_path / "unknown" / source_unknown.name).exists()
        assert not (tmp_path / ".dji-color-classifier" / "manifests").exists()
    finally:
        service.close()


def test_web_service_marks_existing_target_as_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """冲突策略为跳过时，结果统计必须归入跳过而不是成功。"""

    import dji_color_classifier.core.scanner as scanner

    source = tmp_path / "DJI_DLOG.MP4"
    target = tmp_path / "dlog" / source.name
    source.write_bytes(b"source")
    target.parent.mkdir()
    target.write_bytes(b"existing")
    monkeypatch.setattr(scanner, "classify_file", fake_result)

    service = ApplicationService(max_workers=1)
    try:
        scan = scan_directory(service, tmp_path)
        handle = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy", "conflict_policy": "skip"})
        result = wait_task(service, handle["task_id"])["result"]
        assert result["success_count"] == 0
        # 已存在的分类目录中的同名文件本身也会被扫描，并因无需处理而跳过。
        assert result["skipped_count"] == 2
        assert result["failed_count"] == 0
    finally:
        service.close()


def test_web_service_rejects_parallel_organize_for_same_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一目录执行期间不得提交第二个整理任务。"""

    import dji_color_classifier.core.executor as executor
    import dji_color_classifier.core.scanner as scanner

    source = tmp_path / "DJI_DLOG.MP4"
    source.write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    started = threading.Event()
    original_execute_item = executor._execute_item

    def slow_execute_item(item):  # noqa: ANN001
        started.set()
        time.sleep(0.1)
        return original_execute_item(item)

    monkeypatch.setattr(executor, "_execute_item", slow_execute_item)
    service = ApplicationService(max_workers=2)
    try:
        scan = scan_directory(service, tmp_path)
        first = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy"})
        assert started.wait(timeout=1)
        with pytest.raises(RuntimeError, match="已有整理任务"):
            service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy"})
        assert wait_task(service, first["task_id"])["state"] == "completed"
    finally:
        service.close()


def test_web_service_returns_partial_result_after_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消整理仍必须返回取消前完成、跳过和失败的统计。"""

    import dji_color_classifier.core.executor as executor
    import dji_color_classifier.core.scanner as scanner

    for index in range(3):
        (tmp_path / f"DJI_{index}_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    started = threading.Event()
    original_execute_item = executor._execute_item

    def slow_execute_item(item):  # noqa: ANN001
        started.set()
        time.sleep(0.04)
        return original_execute_item(item)

    monkeypatch.setattr(executor, "_execute_item", slow_execute_item)
    service = ApplicationService(max_workers=1)
    try:
        scan = scan_directory(service, tmp_path)
        handle = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy"})
        assert started.wait(timeout=1)
        service.cancel_task(handle["task_id"])
        snapshot = wait_task(service, handle["task_id"])
        assert snapshot["state"] == "cancelled", snapshot
        assert snapshot["result"]["cancelled"] is True
        assert snapshot["result"]["success_count"] >= 1
    finally:
        service.close()
