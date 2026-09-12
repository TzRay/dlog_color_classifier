"""从扫描结果生成文件整理计划。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dji_color_classifier.core.models import ColorMode, ConflictPolicy, PlanAction, PlanItem, ScanResult


DEFAULT_PREFIXES = {
    ColorMode.DLOG: "dlog_",
    ColorMode.DLOG2: "dlog2_",
    ColorMode.REC2100_HLG: "hlg_",
}

DEFAULT_DIRS = {
    ColorMode.DLOG: "dlog",
    ColorMode.DLOG2: "dlog2",
    ColorMode.REC709: "rec709",
    ColorMode.REC2100_HLG: "hlg",
    ColorMode.UNKNOWN: "unknown",
}

SIDECAR_SUFFIXES = {".srt", ".lrf", ".thm", ".jpg", ".jpeg", ".xml"}


def build_plan(
    results: list[ScanResult],
    *,
    root: Path,
    mode: str,
    conflict_policy: ConflictPolicy = ConflictPolicy.ERROR,
    name_template: str | None = None,
    dir_template: str | None = None,
    with_sidecars: bool = False,
) -> list[PlanItem]:
    """根据扫描结果生成整理计划。"""

    if mode not in {"prefix", "move", "copy"}:
        raise ValueError(f"不支持的整理模式：{mode}")

    plan: list[PlanItem] = []
    planned_targets: set[Path] = set()
    sidecar_indexes: dict[Path, dict[str, list[Path]]] = {}
    for result in results:
        item = _build_item(
            result,
            root=root,
            mode=mode,
            name_template=name_template,
            dir_template=dir_template,
        )
        group = [item]
        if with_sidecars and item.target is not None and not item.skipped and item.action is not PlanAction.NONE:
            directory = item.source.parent
            if directory not in sidecar_indexes:
                sidecar_indexes[directory] = _index_sidecars(directory)
            group.extend(_build_sidecar_items(item, sidecar_indexes[directory].get(item.source.stem, [])))

        group = _resolve_group_conflicts(group, planned_targets, conflict_policy)
        for grouped_item in group:
            if grouped_item.target is not None and not grouped_item.skipped and not grouped_item.blocked:
                planned_targets.add(grouped_item.target.resolve())
        plan.extend(group)
    return plan


def _build_item(
    result: ScanResult,
    *,
    root: Path,
    mode: str,
    name_template: str | None,
    dir_template: str | None,
) -> PlanItem:
    """生成单个计划项。"""

    if result.evidence.primary_source == "conflict":
        return PlanItem(result.path, None, PlanAction.NONE, result, skipped=True, reason="元数据证据冲突，禁止自动整理")

    if result.mode in {ColorMode.ERROR, ColorMode.UNKNOWN} and mode == "prefix":
        return PlanItem(result.path, None, PlanAction.NONE, result, skipped=True, reason="无需添加前缀")

    if mode == "prefix":
        target = _prefix_target(result, name_template)
        action = PlanAction.RENAME
    else:
        target = _directory_target(result, root=root, dir_template=dir_template)
        action = PlanAction.MOVE if mode == "move" else PlanAction.COPY

    if target is None or target == result.path:
        return PlanItem(result.path, None, PlanAction.NONE, result, skipped=True, reason="无需处理")

    return PlanItem(result.path, target, action, result)


def _prefix_target(result: ScanResult, name_template: str | None) -> Path | None:
    """生成前缀重命名目标。"""

    path = result.path
    if result.mode not in {ColorMode.DLOG, ColorMode.DLOG2, ColorMode.REC2100_HLG}:
        return None

    lower_name = path.name.lower()
    if lower_name.startswith(("dlog_", "dlog2_", "hlg_", "dlog", "dlog2", "hlg")):
        return None

    if name_template:
        name = _render_name_template(name_template, result)
    else:
        prefix = DEFAULT_PREFIXES.get(result.mode)
        if prefix is None:
            return None
        name = f"{prefix}{path.name}"
    return path.with_name(name)


def _directory_target(result: ScanResult, *, root: Path, dir_template: str | None) -> Path:
    """生成移动或复制到分类目录的目标。"""

    directory_name = _render_directory_template(dir_template, result) if dir_template else DEFAULT_DIRS.get(result.mode, "unknown")
    return root / directory_name / result.path.name


def _render_template(template: str, result: ScanResult) -> str:
    """渲染简单文件名或目录模板。"""

    path = result.path
    return template.format(
        original=path.name,
        stem=path.stem,
        suffix=path.suffix,
        mode=result.mode.value,
        mode_label=result.mode.label,
    )


def _render_name_template(template: str, result: ScanResult) -> str:
    """渲染并校验文件名模板，禁止生成路径或空文件名。"""

    name = _render_template(template, result).strip()
    if not name:
        raise ValueError("文件名模板不能生成空文件名")
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("文件名模板只能生成文件名，不能包含目录")
    return name


def _render_directory_template(template: str, result: ScanResult) -> Path:
    """渲染并校验目录模板，禁止绝对路径和向上跳转。"""

    value = _render_template(template, result).strip()
    directory = Path(value)
    if not value:
        raise ValueError("目录模板不能生成空目录")
    if directory.is_absolute() or any(part == ".." for part in directory.parts):
        raise ValueError("目录模板必须是当前视频目录下的相对路径")
    return directory


def _resolve_group_conflicts(
    items: list[PlanItem], planned_targets: set[Path], conflict_policy: ConflictPolicy
) -> list[PlanItem]:
    """视频与伴随文件作为一组处理冲突，确保最终仍可按同名关联。"""

    video = items[0]
    if video.target is None or video.skipped:
        return items
    if not any(_target_conflicts(item.target, planned_targets) for item in items):
        return items
    if conflict_policy is ConflictPolicy.ERROR:
        return [replace(item, blocked=True, reason="视频或伴随文件的目标已存在，整组未执行") for item in items]
    if conflict_policy is ConflictPolicy.SKIP:
        return [replace(item, skipped=True, reason="视频或伴随文件存在目标冲突，整组已跳过") for item in items]

    index = 1
    while True:
        stem = f"{video.target.stem}_{index:03d}"
        candidates = [
            replace(item, target=item.target.with_name(f"{stem}{item.target.suffix}"))
            for item in items
            if item.target is not None
        ]
        if not any(_target_conflicts(item.target, planned_targets) for item in candidates):
            return candidates
        index += 1


def _target_conflicts(target: Path | None, planned_targets: set[Path]) -> bool:
    """同时检查磁盘、悬空符号链接以及本批次已保留的目标。"""

    return target is not None and (target.exists() or target.is_symlink() or target.resolve() in planned_targets)


def _index_sidecars(directory: Path) -> dict[str, list[Path]]:
    """每个目录仅遍历一次，按 basename 索引伴随文件。"""

    index: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir(), key=lambda path: path.name):
        if path.suffix.lower() in SIDECAR_SUFFIXES and path.is_file():
            index.setdefault(path.stem, []).append(path)
    return index


def _build_sidecar_items(
    video_item: PlanItem,
    sidecars: list[Path],
) -> list[PlanItem]:
    """从目录索引生成伴随文件计划，冲突稍后交由整组统一处理。"""

    assert video_item.target is not None
    items: list[PlanItem] = []
    for sidecar in sidecars:
        if sidecar == video_item.source:
            continue

        target = video_item.target.with_suffix(sidecar.suffix)
        sidecar_result = ScanResult(
            path=sidecar,
            mode=video_item.scan_result.mode,
            evidence=video_item.scan_result.evidence,
            size=sidecar.stat().st_size,
        )
        items.append(PlanItem(sidecar, target, video_item.action, sidecar_result))
    return items
