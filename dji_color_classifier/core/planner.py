"""从扫描结果生成文件整理计划。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.models import ColorMode, ConflictPolicy, PlanAction, PlanItem, ScanResult


DEFAULT_PREFIXES = {
    ColorMode.DLOG: "dlog_",
    ColorMode.DLOG2: "dlog2_",
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
    for result in results:
        item = _build_item(
            result,
            root=root,
            mode=mode,
            conflict_policy=conflict_policy,
            name_template=name_template,
            dir_template=dir_template,
            planned_targets=planned_targets,
        )
        if item.target is not None:
            planned_targets.add(item.target.resolve())
        plan.append(item)
        if with_sidecars and item.target is not None and not item.skipped and item.action is not PlanAction.NONE:
            sidecars = _build_sidecar_items(item, planned_targets)
            for sidecar in sidecars:
                if sidecar.target is not None:
                    planned_targets.add(sidecar.target.resolve())
            plan.extend(sidecars)
    return plan


def _build_item(
    result: ScanResult,
    *,
    root: Path,
    mode: str,
    conflict_policy: ConflictPolicy,
    name_template: str | None,
    dir_template: str | None,
    planned_targets: set[Path],
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

    conflict = target.exists() or target.resolve() in planned_targets
    if conflict:
        if conflict_policy is ConflictPolicy.ERROR:
            return PlanItem(result.path, target, action, result, skipped=True, reason="目标文件已存在")
        if conflict_policy is ConflictPolicy.SKIP:
            return PlanItem(result.path, target, action, result, skipped=True, reason="目标冲突，已跳过")
        target = _append_suffix_until_free(target, planned_targets)

    return PlanItem(result.path, target, action, result)


def _prefix_target(result: ScanResult, name_template: str | None) -> Path | None:
    """生成前缀重命名目标。"""

    path = result.path
    if result.mode not in {ColorMode.DLOG, ColorMode.DLOG2}:
        return None

    lower_name = path.name.lower()
    if lower_name.startswith(("dlog_", "dlog2_", "dlog", "dlog2")):
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


def _append_suffix_until_free(path: Path, planned_targets: set[Path]) -> Path:
    """目标冲突时追加序号，直到找到可用路径。"""

    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists() and candidate.resolve() not in planned_targets:
            return candidate
        index += 1


def _build_sidecar_items(video_item: PlanItem, planned_targets: set[Path]) -> list[PlanItem]:
    """为视频的同名伴随文件生成同步整理计划。"""

    assert video_item.target is not None
    items: list[PlanItem] = []
    for sidecar in video_item.source.parent.iterdir():
        if not sidecar.is_file() or sidecar == video_item.source:
            continue
        if sidecar.stem != video_item.source.stem:
            continue
        if sidecar.suffix.lower() not in SIDECAR_SUFFIXES:
            continue

        target = video_item.target.with_suffix(sidecar.suffix)
        if target.exists() or target.resolve() in planned_targets:
            target = _append_suffix_until_free(target, planned_targets)

        sidecar_result = ScanResult(
            path=sidecar,
            mode=video_item.scan_result.mode,
            evidence=video_item.scan_result.evidence,
            size=sidecar.stat().st_size,
        )
        items.append(PlanItem(sidecar, target, video_item.action, sidecar_result))
    return items
