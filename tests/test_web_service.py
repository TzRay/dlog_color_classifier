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
        assert all(record["status"] == "skipped" for record in result["records"])
    finally:
        service.close()


def test_web_service_marks_error_policy_conflict_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """“标记为失败”策略必须生成失败记录，而不能悄悄归入跳过。"""

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
        handle = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy", "conflict_policy": "error"})
        result = wait_task(service, handle["task_id"])["result"]
        assert result["failed_count"] == 1
        assert result["skipped_count"] == 1
        failed = [record for record in result["records"] if not record["success"]]
        assert len(failed) == 1
        assert "目标" in failed[0]["message"] and "已存在" in failed[0]["message"]
        assert failed[0]["status"] == "failed"
    finally:
        service.close()


def test_web_service_enumerates_directory_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web 总数和实际扫描复用枚举，避免网络盘重复遍历。"""

    import dji_color_classifier.core.scanner as scanner

    (tmp_path / "DJI_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    original = scanner.os.scandir
    visited = []

    def counted(path):
        visited.append(Path(path))
        return original(path)

    monkeypatch.setattr(scanner.os, "scandir", counted)
    service = ApplicationService(max_workers=1)
    try:
        assert scan_directory(service, tmp_path)["summary"]["total"] == 1
        assert visited == [tmp_path]
    finally:
        service.close()


def test_web_service_requires_rescan_after_organize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前端响应丢失或重复请求也不能使用旧扫描再操作一次。"""

    import dji_color_classifier.core.scanner as scanner

    (tmp_path / "DJI_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    service = ApplicationService(max_workers=1)
    try:
        scan = scan_directory(service, tmp_path)
        request = {"scan_id": scan["scan_id"], "mode": "copy"}
        result = wait_task(service, service.execute_organize(request)["task_id"])
        assert result["state"] == "completed"
        with pytest.raises(ValueError, match="重新识别"):
            service.execute_organize(request)
        assert len(list((tmp_path / "dlog").glob("*.MP4"))) == 1
        report = service.export_report({"scan_id": scan["scan_id"], "output": str(tmp_path / "report.csv")})
        assert report["count"] == 1
    finally:
        service.close()


def test_cancel_terminal_task_preserves_finished_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """迟到的取消请求不能把完成的任务重新标为取消。"""

    import dji_color_classifier.core.scanner as scanner

    (tmp_path / "DJI_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    service = ApplicationService(max_workers=1)
    try:
        handle = service.start_scan(str(tmp_path))
        before = wait_task(service, handle["task_id"])
        after = service.cancel_task(handle["task_id"])
        assert after == before
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

    def slow_execute_item(item, **kwargs):  # noqa: ANN001
        started.set()
        time.sleep(0.1)
        return original_execute_item(item, **kwargs)

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
    release = threading.Event()
    original_execute_item = executor._execute_item
    calls = 0

    def slow_execute_item(item, **kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            assert release.wait(timeout=2)
        return original_execute_item(item, **kwargs)

    monkeypatch.setattr(executor, "_execute_item", slow_execute_item)
    service = ApplicationService(max_workers=1)
    try:
        scan = scan_directory(service, tmp_path)
        handle = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy"})
        assert started.wait(timeout=1)
        service.cancel_task(handle["task_id"])
        release.set()
        snapshot = wait_task(service, handle["task_id"])
        assert snapshot["state"] == "cancelled", snapshot
        assert snapshot["result"]["cancelled"] is True
        assert snapshot["result"]["success_count"] == 1
        assert snapshot["result"]["pending_count"] == 2
        assert len(list((tmp_path / "dlog").glob("*.MP4"))) == 1
        assert not list((tmp_path / "dlog").glob(".dji-copy-*"))
    finally:
        release.set()
        service.close()


def test_web_service_close_stops_remaining_organize_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭窗口时中止尚未发布的复制，不得在后台继续处理整个目录。"""

    import dji_color_classifier.core.executor as executor
    import dji_color_classifier.core.scanner as scanner

    for index in range(3):
        (tmp_path / f"DJI_{index}_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    started = threading.Event()
    original_execute_item = executor._execute_item

    def slow_execute_item(item, **kwargs):  # noqa: ANN001
        started.set()
        time.sleep(0.04)
        return original_execute_item(item, **kwargs)

    monkeypatch.setattr(executor, "_execute_item", slow_execute_item)
    service = ApplicationService(max_workers=1)
    scan = scan_directory(service, tmp_path)
    handle = service.execute_organize({"scan_id": scan["scan_id"], "mode": "copy"})
    assert started.wait(timeout=1)

    service.close()

    snapshot = service.get_task_status(handle["task_id"])
    assert snapshot["state"] == "cancelled"
    assert not list((tmp_path / "dlog").glob("*.MP4"))
    assert snapshot["result"]["pending_count"] == 3


@pytest.mark.parametrize("first_kind", ["scan", "organize"])
@pytest.mark.parametrize(
    ("organize_relative", "scan_relative"),
    [(".", "."), (".", "child"), ("child", ".")],
    ids=["same-root", "scan-child", "scan-parent"],
)
def test_web_service_blocks_overlapping_scan_and_organize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_kind: str,
    organize_relative: str,
    scan_relative: str,
) -> None:
    """扫描与整理在两个提交顺序下均互斥，任务结束后应释放父子目录边界。"""

    import dji_color_classifier.core.executor as executor
    import dji_color_classifier.core.scanner as scanner

    media_root = tmp_path / "media"
    child = media_root / "child"
    child.mkdir(parents=True)
    (child / "DJI_DLOG.MP4").write_bytes(b"dlog")
    organize_root = media_root / organize_relative
    scan_root = media_root / scan_relative
    unrelated_root = tmp_path / "unrelated"
    unrelated_root.mkdir()
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    started = threading.Event()
    release = threading.Event()
    original_execute_item = executor._execute_item

    def blocked_classify(path: Path) -> ScanResult:
        """停在扫描单个文件期间，让另一入口的提交顺序完全可控。"""

        started.set()
        assert release.wait(timeout=5), "测试未及时释放扫描任务"
        return fake_result(path)

    def blocked_execute(item, **kwargs):  # noqa: ANN001
        """停在真实文件操作之前，验证整理任务已占用目录。"""

        started.set()
        assert release.wait(timeout=5), "测试未及时释放整理任务"
        return original_execute_item(item, **kwargs)

    service = ApplicationService(max_workers=2)
    try:
        baseline = scan_directory(service, organize_root)
        request = {"scan_id": baseline["scan_id"], "mode": "copy"}
        if first_kind == "scan":
            monkeypatch.setattr(scanner, "classify_file", blocked_classify)
            active = service.start_scan({"directory": str(scan_root), "recursive": True})
        else:
            monkeypatch.setattr(executor, "_execute_item", blocked_execute)
            active = service.execute_organize(request)
        assert started.wait(timeout=5), "后台任务未进入预期阻塞点"

        if first_kind == "scan":
            with pytest.raises(RuntimeError, match="正在识别"):
                service.execute_organize(request)
        else:
            with pytest.raises(RuntimeError, match="已有整理任务"):
                service.start_scan({"directory": str(scan_root), "recursive": True})

        # 互斥只覆盖重叠路径；不相关的目录仍可由另一工作线程正常扫描。
        assert scan_directory(service, unrelated_root)["summary"]["total"] == 0
        assert service.get_state()["active_tasks"] == 1
        release.set()
        assert wait_task(service, active["task_id"])["state"] == "completed"

        if first_kind == "scan":
            completed = wait_task(service, service.execute_organize(request)["task_id"])
            assert completed["state"] == "completed"
            assert completed["result"]["success_count"] == 1
        else:
            assert scan_directory(service, scan_root)["summary"]["total"] >= 1
        assert service.get_state()["active_tasks"] == 0
    finally:
        release.set()
        service.close()


def test_web_service_close_finishes_queued_tasks_and_releases_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关闭时排队的扫描和整理也必须到达取消终态，不能留下任务或目录锁。"""

    import dji_color_classifier.core.scanner as scanner
    import dji_color_classifier.web_service as web_service

    active_root = tmp_path / "active"
    queued_scan_root = tmp_path / "queued_scan"
    queued_organize_root = tmp_path / "queued_organize"
    for root in (active_root, queued_scan_root, queued_organize_root):
        root.mkdir()
        (root / "DJI_DLOG.MP4").write_bytes(b"dlog")
    monkeypatch.setattr(scanner, "classify_file", fake_result)
    original_scan_directory = web_service.scan_directory
    started = threading.Event()

    def wait_for_close(directory: Path, **kwargs):  # noqa: ANN003
        """由 close 的真实取消事件释放工作线程，避免用休眠猜测排队时机。"""

        if directory == active_root:
            started.set()
            assert kwargs["cancel_event"].wait(timeout=5), "关闭操作未发送取消信号"
        return original_scan_directory(directory, **kwargs)

    service = ApplicationService(max_workers=1)
    try:
        baseline = scan_directory(service, queued_organize_root)
        monkeypatch.setattr(web_service, "scan_directory", wait_for_close)
        active = service.start_scan(str(active_root))
        assert started.wait(timeout=5), "首个扫描任务未占用工作线程"
        queued_scan = service.start_scan(str(queued_scan_root))
        queued_organize = service.execute_organize({"scan_id": baseline["scan_id"], "mode": "copy"})
        for handle in (queued_scan, queued_organize):
            assert service.get_task_status(handle["task_id"])["state"] == "queued"
        assert service.get_state()["active_tasks"] == 3

        service.close()

        for handle in (active, queued_scan, queued_organize):
            snapshot = service.get_task_status(handle["task_id"])
            assert snapshot["state"] == "cancelled"
            assert snapshot["finished_at"] is not None
            assert snapshot["result"]["cancelled"] is True
        assert service.get_state()["active_tasks"] == 0
        assert not service._scanning_roots
        assert not service._organizing_roots
        assert not (queued_organize_root / "dlog").exists()
        assert (queued_organize_root / "DJI_DLOG.MP4").read_bytes() == b"dlog"
    finally:
        service.close()
