"""Web 应用服务验收测试。

测试通过替换扫描器中的单文件识别函数构造轻量素材，重点验证桥接层的
任务状态、JSON DTO、计划执行、manifest 和撤销边界，而不是重复 MP4 解析器测试。
"""

from __future__ import annotations

import time
from pathlib import Path

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
from dji_color_classifier.web_service import ApplicationService


def wait_task(service: ApplicationService, task_id: str) -> dict:
    """等待短任务完成，失败时把结构化错误带回测试。"""

    for _ in range(100):
        snapshot = service.get_task_status(task_id)
        if snapshot["state"] in {"completed", "failed", "cancelled"}:
            assert snapshot["state"] == "completed", snapshot
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"任务未在预期时间内完成：{task_id}")


def fake_result(path: Path) -> ScanResult:
    """根据文件名生成可用于服务层测试的识别结果。"""

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


def test_web_service_scan_plan_execute_and_undo(tmp_path: Path, monkeypatch) -> None:
    """Web API 应完成扫描、生成计划、执行复制并依据 manifest 撤销。"""

    import dji_color_classifier.core.scanner as scanner

    source_dlog = tmp_path / "DJI_DLOG.MP4"
    source_hlg = tmp_path / "DJI_HLG.MP4"
    source_unknown = tmp_path / "DJI_UNKNOWN.MP4"
    for path, content in ((source_dlog, b"dlog"), (source_hlg, b"hlg"), (source_unknown, b"unknown")):
        path.write_bytes(content)
    monkeypatch.setattr(scanner, "classify_file", fake_result)

    service = ApplicationService(max_workers=1)
    try:
        scan_handle = service.start_scan({"directory": str(tmp_path), "recursive": True})
        scan_snapshot = wait_task(service, scan_handle["task_id"])
        scan = scan_snapshot["result"]

        assert scan["summary"]["total"] == 3
        assert scan["summary"]["modes"]["rec2100_hlg"] == 1
        assert scan["results"][0]["path"]

        plan = service.build_plan(
            {
                "scan_id": scan["scan_id"],
                "mode": "copy",
                "conflict_policy": "suffix",
                "with_sidecars": False,
            }
        )
        # 按现有核心规格，无法确认的视频进入 unknown/，而不是被静默丢弃。
        assert plan["actionable_count"] == 3
        assert plan["skipped_count"] == 0
        assert any(item["mode"] == "rec2100_hlg" for item in plan["items"])

        prefix_plan = service.build_plan(
            {
                "scan_id": scan["scan_id"],
                "mode": "prefix",
                "conflict_policy": "suffix",
            }
        )
        hlg_prefix = next(item for item in prefix_plan["items"] if item["mode"] == "rec2100_hlg")
        assert hlg_prefix["target"].endswith("hlg_DJI_HLG.MP4")

        execute_handle = service.execute_plan({"plan_id": plan["plan_id"], "confirmed": True})
        execute = wait_task(service, execute_handle["task_id"])["result"]
        assert execute["success_count"] == 3
        manifest_path = Path(execute["manifest_path"])
        assert manifest_path.is_file()
        assert (tmp_path / "dlog" / source_dlog.name).is_file()
        assert (tmp_path / "hlg" / source_hlg.name).is_file()
        assert source_dlog.is_file(), "复制模式必须保留原文件"

        manifest = service.load_manifest({"manifest_path": str(manifest_path)})
        assert manifest["undo"]["actionable_count"] == 3

        undo_handle = service.execute_undo({"manifest_path": str(manifest_path), "confirmed": True})
        undo = wait_task(service, undo_handle["task_id"])["result"]
        assert undo["success_count"] == 3
        assert undo["skipped_count"] == 0
        assert not (tmp_path / "dlog" / source_dlog.name).exists()
        assert not (tmp_path / "hlg" / source_hlg.name).exists()
        assert source_dlog.is_file()
    finally:
        service.close()


def test_web_service_requires_explicit_confirmation(tmp_path: Path) -> None:
    """服务端不能接受前端遗漏的最终确认。"""

    service = ApplicationService(max_workers=1)
    try:
        try:
            service.execute_plan({"plan_id": "plan_missing", "confirmed": False})
        except PermissionError as exc:
            assert "明确确认" in str(exc)
        else:
            raise AssertionError("未确认的计划不应进入执行队列")
    finally:
        service.close()


def test_web_service_can_cancel_scan(tmp_path: Path, monkeypatch) -> None:
    """扫描任务收到取消请求后不得继续创建可执行的 scan_id。"""

    import dji_color_classifier.core.scanner as scanner

    for index in range(5):
        path = tmp_path / f"DJI_{index:04d}.MP4"
        path.write_bytes(b"video")

    def slow_result(path: Path) -> ScanResult:
        time.sleep(0.03)
        return fake_result(path)

    monkeypatch.setattr(scanner, "classify_file", slow_result)
    service = ApplicationService(max_workers=1)
    try:
        handle = service.start_scan({"directory": str(tmp_path), "recursive": False})
        service.cancel_task(handle["task_id"])
        for _ in range(100):
            snapshot = service.get_task_status(handle["task_id"])
            if snapshot["state"] in {"cancelled", "failed", "completed"}:
                break
            time.sleep(0.01)
        assert snapshot["state"] == "cancelled", snapshot
        assert "scan_id" not in (snapshot["result"] or {})
    finally:
        service.close()
