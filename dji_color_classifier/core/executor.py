"""执行文件整理计划。"""

from __future__ import annotations

import shutil

from dji_color_classifier.core.models import ExecutionRecord, PlanAction, PlanItem


def execute_plan(plan: list[PlanItem], *, apply: bool = False) -> list[ExecutionRecord]:
    """执行或预演整理计划。"""

    records: list[ExecutionRecord] = []
    for item in plan:
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
            continue

        records.append(_execute_item(item))
    return records


def _execute_item(item: PlanItem) -> ExecutionRecord:
    """执行单个计划项。"""

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
        if item.target.exists():
            raise FileExistsError(f"目标文件已存在：{item.target}")

        if item.action is PlanAction.RENAME or item.action is PlanAction.MOVE:
            item.source.rename(item.target)
        elif item.action is PlanAction.COPY:
            shutil.copy2(item.source, item.target)
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
    except Exception as exc:
        return ExecutionRecord(item.source, item.target, item.action, item.scan_result.mode, False, str(exc))


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
