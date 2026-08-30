"""面向 Web 工作台的任务式应用服务。

Web 工作台只负责选择目录、查看识别结果和直接整理。它不保存预演计划、
操作记录或撤销清单；CLI 和原生 GUI 仍可继续使用 core 层的对应能力。
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

from dji_color_classifier.core.executor import execute_plan as execute_core_plan
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
MAX_RETAINED_TASKS = 100
MAX_RETAINED_SCANS = 20


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
                "progress": self.completed / self.total if self.total else 0,
                "message": self.message,
                "result": self.result,
                "error": self.error,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
            }


class ApplicationService:
    """提供桌面 Web 前端所需的扫描、整理和报告接口。"""

    def __init__(self, *, max_workers: int = 2) -> None:
        """初始化服务及有限线程池。"""

        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dji-color-web")
        self._lock = threading.RLock()
        self._tasks: dict[str, _Task] = {}
        self._scans: dict[str, dict[str, Any]] = {}
        # 同一素材根目录只允许一个整理任务，避免并发计划互相抢占目标路径。
        self._organizing_roots: set[Path] = set()

    def close(self) -> None:
        """取消剩余工作并关闭线程池，应用退出时调用。"""

        with self._lock:
            active_tasks = [task for task in self._tasks.values() if task.state in {"queued", "running"}]
        for task in active_tasks:
            task.cancel_event.set()
        if active_tasks:
            LOGGER.info("应用正在退出，已请求取消 %s 个后台任务", len(active_tasks))
        # 等待当前单个文件完成，避免窗口关闭后仍在后台继续批量移动或复制。
        self._executor.shutdown(wait=True, cancel_futures=True)

    def get_state(self) -> dict[str, Any]:
        """返回前端初始化所需的服务状态。"""

        with self._lock:
            return {
                "connected": True,
                "service": "python-application-service",
                "active_tasks": sum(task.state in {"queued", "running"} for task in self._tasks.values()),
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
                self._trim_retained_state()
            LOGGER.info("扫描完成：%s，共 %s 个视频", root, len(results))
            return {
                "scan_id": scan_id,
                "root": str(root),
                "recursive": recursive,
                "results": [_scan_result_to_dto(item, root) for item in results],
                "summary": _summary_to_dto(results),
            }

        return self._submit("scan", work)

    def execute_organize(self, options: dict[str, Any]) -> dict[str, Any]:
        """按当前设置直接整理已扫描目录，不创建预演或操作记录。"""

        payload = _options_dict(options)
        scan_id = str(payload.get("scan_id", ""))
        with self._lock:
            scan = self._scans.get(scan_id)
        if scan is None:
            raise ValueError(f"扫描结果不存在或已失效：{scan_id}")

        mode = str(payload.get("mode", "copy"))
        if mode not in {"copy", "move", "prefix"}:
            raise ValueError(f"不支持的整理方式：{mode}")
        conflict = ConflictPolicy(str(payload.get("conflict_policy", "suffix")))
        with_sidecars = bool(payload.get("with_sidecars", False))
        root = scan["root"]

        with self._lock:
            if root in self._organizing_roots:
                raise RuntimeError(f"该目录已有整理任务正在执行：{root}")
            self._organizing_roots.add(root)

        LOGGER.info("提交直接整理任务：目录=%s，方式=%s，冲突策略=%s", root, mode, conflict.value)

        def work(task: _Task) -> dict[str, Any]:
            try:
                # 在后台任务开始时即时构建计划，缩短扫描与文件操作之间的时间差。
                plan = _build_web_organize_plan(
                    scan["results"],
                    root=root,
                    mode=mode,
                    conflict_policy=conflict,
                    with_sidecars=with_sidecars,
                )
                records = execute_core_plan(
                    plan,
                    apply=True,
                    cancel_event=task.cancel_event,
                    on_progress=lambda completed, total, item: self._update_progress(
                        task, completed, total, f"正在处理：{item.source.name}"
                    ),
                )
                return _organize_result_to_dto(records, plan=plan, cancelled=task.cancel_event.is_set())
            finally:
                with self._lock:
                    self._organizing_roots.discard(root)

        try:
            return self._submit("organize", work)
        except Exception:
            # 线程池已关闭等提交失败场景不会进入 work 的 finally，须在此释放目录互斥。
            with self._lock:
                self._organizing_roots.discard(root)
            raise

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """读取任务状态。"""

        with self._lock:
            task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        return task.snapshot()

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """请求取消任务；当前单个文件完成后才会停止。"""

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

    def export_report(self, options: dict[str, Any]) -> dict[str, Any]:
        """导出已完成扫描的 CSV 或 JSON 识别报告。"""

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

    def _submit(self, kind: str, work: Callable[[_Task], dict[str, Any]]) -> dict[str, Any]:
        """提交线程池任务并返回轻量句柄。"""

        task = _Task(task_id=_new_id(kind), kind=kind)
        with self._lock:
            self._tasks[task.task_id] = task
        try:
            self._executor.submit(self._run_task, task, work)
        except Exception:
            # 提交失败的任务从未运行，不能让它永久停留在 queued 状态。
            with self._lock:
                self._tasks.pop(task.task_id, None)
            raise
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
                task.message = "任务已取消，已返回部分结果" if task.state == "cancelled" else "任务完成"
                task.finished_at = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            LOGGER.exception("任务失败：%s", task.task_id)
            with task.lock:
                task.state = "failed"
                task.error = f"{type(exc).__name__}: {exc}"
                task.message = "任务失败"
                task.finished_at = datetime.now().isoformat(timespec="seconds")
        finally:
            with self._lock:
                self._trim_retained_state()

    def _trim_retained_state(self) -> None:
        """限制会话内任务和扫描缓存，避免长期运行持续占用内存。

        调用方必须已持有 ``self._lock``；活跃任务不会被移除，以免前端轮询丢失状态。
        """

        while len(self._tasks) > MAX_RETAINED_TASKS:
            removable = next(
                (
                    task_id
                    for task_id, task in self._tasks.items()
                    if task.snapshot()["state"] in {"completed", "failed", "cancelled"}
                ),
                None,
            )
            if removable is None:
                break
            self._tasks.pop(removable)
        while len(self._scans) > MAX_RETAINED_SCANS:
            oldest_scan_id = next(iter(self._scans))
            self._scans.pop(oldest_scan_id)

    @staticmethod
    def _update_progress(task: _Task, completed: int, total: int, message: str) -> None:
        """更新任务进度，避免前端轮询时看到不一致的字段。"""

        with task.lock:
            task.completed = completed
            task.total = total
            task.message = message


def _build_web_organize_plan(
    results: list[ScanResult],
    *,
    root: Path,
    mode: str,
    conflict_policy: ConflictPolicy,
    with_sidecars: bool,
) -> list[PlanItem]:
    """构建 Web 专用计划，未知、冲突和错误结果永不自动整理。"""

    skipped: list[PlanItem] = []
    eligible: list[ScanResult] = []
    for result in results:
        if result.error or result.mode in {ColorMode.UNKNOWN, ColorMode.ERROR}:
            skipped.append(PlanItem(result.path, None, PlanAction.NONE, result, skipped=True, reason="识别结果无法确认，未自动整理"))
        elif result.evidence.primary_source == "conflict":
            skipped.append(PlanItem(result.path, None, PlanAction.NONE, result, skipped=True, reason="元数据证据冲突，未自动整理"))
        else:
            eligible.append(result)

    # core 层仍服务于 CLI/GUI；Web 端仅以已确认结果调用它，避免改变其他入口语义。
    actionable = build_core_plan(
        eligible,
        root=root,
        mode=mode,
        conflict_policy=conflict_policy,
        with_sidecars=with_sidecars,
    )
    if conflict_policy is ConflictPolicy.ERROR:
        # core 计划以 skipped 表示预演阶段发现的冲突；Web 选项明确写作“标记为失败”，
        # 因此让执行器安全地再次检查目标并生成失败记录，保证统计和明细语义一致。
        actionable = [
            PlanItem(item.source, item.target, item.action, item.scan_result, reason=item.reason)
            if item.skipped and item.action is not PlanAction.NONE and item.target is not None
            else item
            for item in actionable
        ]
    return [*actionable, *skipped]


def _organize_result_to_dto(
    records: list[ExecutionRecord],
    *,
    plan: list[PlanItem],
    cancelled: bool,
) -> dict[str, Any]:
    """汇总直接整理结果，确保取消任务也能展示已经发生的文件操作。"""

    # executor 会为 skipped 项保留原 action；因此须结合原计划统计，不能仅看记录 action。
    completed_items = plan[: len(records)]
    actionable_pairs = [
        (item, record)
        for item, record in zip(completed_items, records, strict=True)
        if not item.skipped and item.action is not PlanAction.NONE
    ]
    success_count = sum(record.success for _, record in actionable_pairs)
    failed_count = len(actionable_pairs) - success_count
    skipped_count = len(records) - len(actionable_pairs)
    LOGGER.info("整理任务结束：成功 %s 项，跳过 %s 项，失败 %s 项，取消=%s", success_count, skipped_count, failed_count, cancelled)
    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "cancelled": cancelled,
        "records": [_execution_record_to_dto(record) for record in records],
    }


def _options_dict(options: dict[str, Any] | str) -> dict[str, Any]:
    """统一字符串和对象两种桥接参数形式。"""

    if isinstance(options, str):
        return {"directory": options}
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


def _new_id(prefix: str) -> str:
    """生成前端可读的任务或资源 ID。"""

    return f"{prefix}_{uuid.uuid4().hex}"


def _summary_to_dto(results: list[ScanResult]) -> dict[str, Any]:
    """将核心统计转换为稳定的模式值统计。"""

    counts = {mode.value: 0 for mode in ColorMode}
    for result in results:
        counts[result.mode.value] += 1
    return {"total": len(results), "modes": counts, "labels": summarize_results(results)}


def _scan_result_to_dto(result: ScanResult, root: Path) -> dict[str, Any]:
    """转换扫描结果，补齐 Web 表格所需展示字段。"""

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
        status_label = "不自动整理"
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
