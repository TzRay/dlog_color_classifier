"""面向 Web 前端的应用服务与任务式 JSON API。

该模块是 GUI 与核心模块之间的稳定边界：前端只提交目录、扫描结果 ID、
计划 ID 和 manifest 路径，不直接接触文件系统或低级文件操作。所有路径校验、
冲突处理、manifest 写入和撤销安全检查仍由现有 core 模块负责。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dji_color_classifier.core.executor import build_undo_plan, execute_plan as execute_core_plan
from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import (
    ColorMode,
    ConflictPolicy,
    ExecutionRecord,
    PlanAction,
    PlanItem,
    ScanResult,
)
from dji_color_classifier.core.planner import build_plan as build_core_plan
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import iter_video_files, scan_directory, summarize_results


LOGGER = logging.getLogger(__name__)


@dataclass
class _Task:
    """后台任务的内部状态，不直接暴露给 JavaScript。"""

    task_id: str
    kind: str
    state: str = "queued"
    completed: int = 0
    total: int = 0
    message: str = "等待开始"
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """返回线程安全、可直接 JSON 序列化的任务快照。"""

        with self.lock:
            return {
                "task_id": self.task_id,
                "kind": self.kind,
                "state": self.state,
                "completed": self.completed,
                "total": self.total,
                "progress": (self.completed / self.total if self.total else 0),
                "message": self.message,
                "result": self.result,
                "error": self.error,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
            }


class ApplicationService:
    """提供桌面 Web 前端所需的任务式应用服务。"""

    def __init__(self, *, max_workers: int = 2) -> None:
        """初始化服务及其有限线程池。"""

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dji-color-web",
        )
        self._lock = threading.RLock()
        self._tasks: dict[str, _Task] = {}
        self._scans: dict[str, dict[str, Any]] = {}
        self._plans: dict[str, dict[str, Any]] = {}
        self._last_manifest_path: Path | None = None

    def close(self) -> None:
        """关闭任务线程池，应用退出时调用。"""

        self._executor.shutdown(wait=False, cancel_futures=True)

    def get_state(self) -> dict[str, Any]:
        """返回前端初始化所需的服务状态。"""

        with self._lock:
            return {
                "connected": True,
                "service": "python-application-service",
                "active_tasks": sum(task.state in {"queued", "running"} for task in self._tasks.values()),
                "last_manifest_path": str(self._last_manifest_path) if self._last_manifest_path else None,
            }

    def start_scan(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """创建目录扫描任务，返回 ``task_id``。"""

        payload = _options_dict(options)
        root = _require_directory(payload.get("directory") or payload.get("root"))
        recursive = bool(payload.get("recursive", True))
        LOGGER.info("提交扫描任务：目录=%s，递归=%s", root, recursive)

        def work(task: _Task) -> dict[str, Any]:
            files = iter_video_files(root, recursive=recursive)
            with task.lock:
                task.total = len(files)
                task.message = f"待识别 {len(files)} 个视频"
            results = scan_directory(
                root,
                recursive=recursive,
                cancel_event=task.cancel_event,
                on_progress=lambda completed, total, path: self._update_progress(
                    task, completed, total, f"正在识别：{path.name}"
                ),
            )
            if task.cancel_event.is_set():
                LOGGER.info("扫描任务已取消：%s", task.task_id)
                return {"cancelled": True, "root": str(root), "results": []}
            scan_id = _new_id("scan")
            with self._lock:
                self._scans[scan_id] = {"root": root, "recursive": recursive, "results": results}
            LOGGER.info("扫描完成：%s，共 %s 个视频", root, len(results))
            return {
                "scan_id": scan_id,
                "root": str(root),
                "recursive": recursive,
                "results": [_scan_result_to_dto(item, root) for item in results],
                "summary": _summary_to_dto(results),
            }

        return self._submit("scan", work)

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """读取任务状态。"""

        with self._lock:
            task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        return task.snapshot()

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """请求取消任务；正在进行的单个文件操作不会被强制中断。"""

        with self._lock:
            task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        task.cancel_event.set()
        with task.lock:
            if task.state in {"queued", "running"}:
                task.message = "正在取消，等待当前文件完成"
        LOGGER.info("已请求取消任务：%s", task_id)
        return task.snapshot()

    def build_plan(self, options: dict[str, Any]) -> dict[str, Any]:
        """根据扫描结果生成预演计划，不执行任何文件操作。"""

        payload = _options_dict(options)
        scan_id = str(payload.get("scan_id", ""))
        with self._lock:
            scan = self._scans.get(scan_id)
        if scan is None:
            raise ValueError(f"扫描结果不存在或已失效：{scan_id}")

        mode = str(payload.get("mode", "copy"))
        conflict = ConflictPolicy(str(payload.get("conflict_policy", "suffix")))
        plan = build_core_plan(
            scan["results"],
            root=scan["root"],
            mode=mode,
            conflict_policy=conflict,
            name_template=_optional_text(payload.get("name_template")),
            dir_template=_optional_text(payload.get("dir_template")),
            with_sidecars=bool(payload.get("with_sidecars", False)),
        )
        plan_id = _new_id("plan")
        with self._lock:
            self._plans[plan_id] = {"root": scan["root"], "operation": mode, "items": plan}
        result = _plan_to_dto(plan_id, scan_id, scan["root"], mode, plan)
        LOGGER.info("生成整理计划：%s，可处理 %s 项，跳过 %s 项", plan_id, result["actionable_count"], result["skipped_count"])
        return result

    def execute_plan(self, options: dict[str, Any]) -> dict[str, Any]:
        """创建已确认的整理执行任务并返回 ``task_id``。"""

        payload = _options_dict(options)
        plan_id = str(payload.get("plan_id", ""))
        if payload.get("confirmed") is not True:
            raise PermissionError("执行整理前必须明确确认计划")
        with self._lock:
            stored = self._plans.get(plan_id)
        if stored is None:
            raise ValueError(f"整理计划不存在或已失效：{plan_id}")
        return self._submit("execution", lambda task: self._execute_stored_plan(task, plan_id, stored))

    def export_report(self, options: dict[str, Any]) -> dict[str, Any]:
        """导出已完成扫描的 CSV/JSON 报告。"""

        payload = _options_dict(options)
        scan_id = str(payload.get("scan_id", ""))
        output_value = payload.get("output")
        fmt = str(payload.get("format", "csv"))
        with self._lock:
            scan = self._scans.get(scan_id)
        if scan is None:
            raise ValueError(f"扫描结果不存在或已失效：{scan_id}")
        output = Path(str(output_value)).expanduser() if output_value else _default_report_path(scan["root"], fmt)
        write_report(scan["results"], output, fmt=fmt)
        LOGGER.info("报告已导出：%s", output)
        return {"path": str(output.resolve()), "format": fmt, "count": len(scan["results"])}

    def load_manifest(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """读取 manifest 并返回可撤销预览。"""

        payload = _options_dict(options)
        path = _require_file(payload.get("manifest_path") or payload.get("path"))
        manifest = read_manifest(path)
        undo_plan = build_undo_plan(manifest.records)
        LOGGER.info("载入操作记录：%s，共 %s 条记录", path, len(manifest.records))
        return {
            "manifest_path": str(path),
            "manifest": manifest.to_dict(),
            "undo": _plan_to_dto("", "", manifest.root, "undo", undo_plan),
        }

    def preview_undo(self, options: dict[str, Any] | str) -> dict[str, Any]:
        """读取撤销计划，保留独立方法供前端显式调用。"""

        return self.load_manifest(options)

    def execute_undo(self, options: dict[str, Any]) -> dict[str, Any]:
        """创建已确认的撤销任务。"""

        payload = _options_dict(options)
        path = _require_file(payload.get("manifest_path") or payload.get("path"))
        if payload.get("confirmed") is not True:
            raise PermissionError("执行撤销前必须明确确认计划")
        manifest = read_manifest(path)
        plan = build_undo_plan(manifest.records)
        on_missing = str(payload.get("on_missing", "error"))
        if on_missing == "skip":
            plan = [item for item in plan if item.source.exists()]
        else:
            missing = next((item.source for item in plan if not item.source.exists()), None)
            if missing is not None:
                raise FileNotFoundError(f"撤销源文件不存在：{missing}")
        stored = {"root": manifest.root, "operation": "undo", "items": plan}
        return self._submit("undo", lambda task: self._execute_stored_plan(task, "", stored))

    def _execute_stored_plan(self, task: _Task, plan_id: str, stored: dict[str, Any]) -> dict[str, Any]:
        """执行计划、写入 manifest 并返回前端 DTO。"""

        plan: list[PlanItem] = stored["items"]
        records = execute_core_plan(
            plan,
            apply=True,
            cancel_event=task.cancel_event,
            on_progress=lambda completed, total, item: self._update_progress(
                task, completed, total, f"正在处理：{item.source.name}"
            ),
        )
        manifest = create_manifest(stored["root"], stored["operation"], records)
        manifest_path = write_manifest(manifest)
        with self._lock:
            self._last_manifest_path = manifest_path
        actionable_records = [record for record in records if record.action is not PlanAction.NONE]
        success_count = sum(record.success for record in actionable_records)
        failed_count = len(actionable_records) - success_count
        skipped_count = len(records) - len(actionable_records)
        LOGGER.info("整理任务完成：成功 %s 项，失败 %s 项，记录=%s", success_count, failed_count, manifest_path)
        return {
            "plan_id": plan_id,
            "manifest_path": str(manifest_path),
            "records": [_execution_record_to_dto(record) for record in records],
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
        }

    def _submit(self, kind: str, work: Callable[[_Task], dict[str, Any]]) -> dict[str, Any]:
        """提交线程池任务并返回轻量句柄。"""

        task = _Task(task_id=_new_id(kind), kind=kind)
        with self._lock:
            self._tasks[task.task_id] = task
        self._executor.submit(self._run_task, task, work)
        return {"task_id": task.task_id, "kind": kind, "state": task.state}

    def _run_task(self, task: _Task, work: Callable[[_Task], dict[str, Any]]) -> None:
        """在线程池中运行任务并统一收敛状态。"""

        with task.lock:
            task.state = "running"
            task.message = "正在处理"
        try:
            result = work(task)
            with task.lock:
                task.result = result
                task.state = "cancelled" if task.cancel_event.is_set() else "completed"
                task.message = "任务已取消" if task.state == "cancelled" else "任务完成"
                task.finished_at = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:  # 单个 API 任务失败必须转为结构化错误
            LOGGER.exception("任务失败：%s", task.task_id)
            with task.lock:
                task.state = "failed"
                task.error = f"{type(exc).__name__}: {exc}"
                task.message = "任务失败"
                task.finished_at = datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _update_progress(task: _Task, completed: int, total: int, message: str) -> None:
        """更新任务进度，避免前端轮询时看到不一致的字段。"""

        with task.lock:
            task.completed = completed
            task.total = total
            task.message = message


def _options_dict(options: dict[str, Any] | str) -> dict[str, Any]:
    """统一字符串和对象两种桥接参数形式。"""

    if isinstance(options, str):
        return {"directory": options, "path": options, "manifest_path": options}
    if not isinstance(options, dict):
        raise TypeError("API 参数必须是对象或字符串")
    return options


def _require_directory(value: Any) -> Path:
    """校验并规范化目录路径。"""

    if not value:
        raise ValueError("必须提供扫描目录")
    path = Path(str(value)).expanduser().resolve()
    if not path.is_dir():
        raise NotADirectoryError(f"目录不存在：{path}")
    return path


def _require_file(value: Any) -> Path:
    """校验并规范化文件路径。"""

    if not value:
        raise ValueError("必须提供文件路径")
    path = Path(str(value)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    return path


def _optional_text(value: Any) -> str | None:
    """将空字符串归一化为 None。"""

    text = str(value).strip() if value is not None else ""
    return text or None


def _new_id(prefix: str) -> str:
    """生成前端可读的短任务/资源 ID。"""

    return f"{prefix}_{uuid.uuid4().hex}"


def _summary_to_dto(results: list[ScanResult]) -> dict[str, Any]:
    """将核心统计转换为稳定的模式值统计。"""

    counts = {mode.value: 0 for mode in ColorMode}
    for result in results:
        counts[result.mode.value] += 1
    return {"total": len(results), "modes": counts, "labels": summarize_results(results)}


def _scan_result_to_dto(result: ScanResult, root: Path) -> dict[str, Any]:
    """转换扫描结果，补齐 Web 表格所需的展示字段。"""

    try:
        relative_parent = result.path.parent.resolve().relative_to(root.resolve())
        folder = relative_parent.as_posix() if str(relative_parent) != "." else "当前目录"
    except ValueError:
        folder = result.path.parent.as_posix()
    if result.error:
        status = "error"
        status_label = "识别失败"
    elif result.mode is ColorMode.UNKNOWN or result.evidence.primary_source == "conflict":
        status = "review"
        status_label = "待确认"
    else:
        status = "ready"
        status_label = "已识别"
    return {
        "path": str(result.path),
        "name": result.path.name,
        "relative_path": result.path.resolve().relative_to(root.resolve()).as_posix()
        if result.path.resolve().is_relative_to(root.resolve())
        else result.path.name,
        "folder": folder,
        "mode": result.mode.value,
        "label": result.mode.label,
        "status": status,
        "status_label": status_label,
        "size": result.size,
        "evidence": _evidence_label(result),
        "evidence_detail": result.evidence.detail,
        "confidence": result.evidence.confidence,
        "error": result.error,
        "warnings": list(result.evidence.warnings),
    }


def _evidence_label(result: ScanResult) -> str:
    """生成适合结果表格的短证据说明。"""

    if result.error:
        return "识别失败"
    if result.evidence.primary_source == "conflict":
        return "证据冲突"
    if result.evidence.primary_source == "quicktime_mdta":
        return "QuickTime 标签"
    if result.evidence.primary_source == "djmd_gamma_enum":
        return f"djmd · {_confidence_label(result.evidence.confidence)}"
    if result.evidence.primary_source == "djmd_record_mode":
        return "djmd · 兼容规则"
    return "无可用证据"


def _confidence_label(confidence: str) -> str:
    """将内部置信度映射为中文。"""

    return {"high": "高置信度", "medium": "中置信度", "low": "低置信度"}.get(confidence, "待确认")


def _plan_to_dto(plan_id: str, scan_id: str, root: Path, operation: str, plan: list[PlanItem]) -> dict[str, Any]:
    """转换整理计划并计算前端摘要。"""

    items = [_plan_item_to_dto(item, root) for item in plan]
    actionable = [item for item in plan if not item.skipped and item.action is not PlanAction.NONE and item.target]
    skipped = [item for item in plan if item.skipped or item.action is PlanAction.NONE]
    estimated_bytes = sum(item.scan_result.size for item in actionable if item.action is PlanAction.COPY)
    return {
        "plan_id": plan_id,
        "scan_id": scan_id,
        "operation": operation,
        "root": str(root),
        "items": items,
        "actionable_count": len(actionable),
        "skipped_count": len(skipped),
        "estimated_bytes": estimated_bytes,
        "has_conflicts": any(item["status"] == "conflict" for item in items),
        "summary": _plan_summary(plan),
    }


def _plan_item_to_dto(item: PlanItem, root: Path) -> dict[str, Any]:
    """转换单个计划项。"""

    if item.skipped:
        status = (
            "conflict"
            if item.reason and ("冲突" in item.reason or "已存在" in item.reason)
            else "skip"
        )
    else:
        status = "ready"
    return {
        "source": str(item.source),
        "target": str(item.target) if item.target else None,
        "name": item.source.name,
        "action": item.action.value,
        "mode": item.scan_result.mode.value,
        "mode_label": item.scan_result.mode.label,
        "status": status,
        "skipped": item.skipped,
        "reason": item.reason,
        "size": item.scan_result.size,
        "source_relative": _relative_path(item.source, root),
        "target_relative": _relative_path(item.target, root) if item.target else None,
    }


def _plan_summary(plan: list[PlanItem]) -> dict[str, int]:
    """按色彩模式统计可执行项和跳过项。"""

    summary = {mode.value: 0 for mode in ColorMode}
    for item in plan:
        if not item.skipped and item.action is not PlanAction.NONE:
            summary[item.scan_result.mode.value] += 1
    return summary


def _execution_record_to_dto(record: ExecutionRecord) -> dict[str, Any]:
    """转换执行结果。"""

    return {
        "source": str(record.source),
        "target": str(record.target) if record.target else None,
        "action": record.action.value,
        "mode": record.mode.value,
        "mode_label": record.mode.label,
        "success": record.success,
        "message": record.message,
        "source_size": record.source_size,
        "target_size": record.target_size,
    }


def _relative_path(path: Path | None, root: Path) -> str | None:
    """返回稳定的相对路径，越界时保留绝对路径以便暴露安全问题。"""

    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _default_report_path(root: Path, fmt: str) -> Path:
    """生成不覆盖已有报告的默认输出路径。"""

    suffix = ".json" if fmt == "json" else ".csv"
    candidate = root / ".dji-color-classifier" / "reports" / f"scan_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
    index = 1
    while candidate.exists():
        candidate = candidate.with_name(f"{candidate.stem}_{index}{suffix}")
        index += 1
    return candidate


__all__ = ["ApplicationService"]
