"""DJI 视频色彩模式的多证据判定。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
from dji_color_classifier.core.mp4_reader import Mp4ReaderError, read_dji_color_gamma_label, read_first_djmd_packet
from dji_color_classifier.core.proto_reader import parse_proto, value_at_path


METADATA_LABEL_MODES = {
    "d-log": ColorMode.DLOG,
    "d-log2": ColorMode.DLOG2,
    "rec.709": ColorMode.REC709,
    "rec.2100 hlg": ColorMode.REC2100_HLG,
}


def classify_djmd_packet(
    packet: bytes, *, metadata_label: str | None = None
) -> tuple[ColorMode, ClassificationEvidence]:
    """根据 ``djmd`` 第一包及可选的 QuickTime 标签判定，保留旧接口兼容性。"""

    color_gamma_sxs, record_mode = _read_djmd_values(packet)
    return _classify_evidence(
        color_gamma_sxs=color_gamma_sxs,
        record_mode=record_mode,
        metadata_label=metadata_label,
    )


def classify_file(video_path: Path) -> ScanResult:
    """识别单个 DJI 视频文件，并合并 ``mdta`` 与 ``djmd`` 证据。

    QuickTime ``ColorGammaSxS`` 文本标签是文件内的明确标注，优先级高于 DJI
    私有 protobuf 枚举。两个可靠来源冲突时返回“无法确认”，避免静默误整理。
    """

    warnings: list[str] = []
    metadata_label: str | None = None
    packet: bytes | None = None
    fatal_errors: list[str] = []

    try:
        metadata_label = read_dji_color_gamma_label(video_path)
    except Mp4ReaderError as exc:
        message = str(exc)
        warnings.append(f"QuickTime 元数据读取失败：{message}")
        if "未找到 moov box" in message:
            fatal_errors.append(message)
    except OSError as exc:
        fatal_errors.append(f"无法读取文件：{exc}")

    try:
        packet = read_first_djmd_packet(video_path)
    except Mp4ReaderError as exc:
        message = str(exc)
        # 缺少 djmd 轨是合法情况；仍可能通过 QuickTime 标签分类。
        if "未找到 DJI djmd 数据轨" not in message:
            warnings.append(f"djmd 元数据读取失败：{message}")
            if "未找到 moov box" in message:
                fatal_errors.append(message)
    except OSError as exc:
        fatal_errors.append(f"无法读取文件：{exc}")

    if metadata_label is None and packet is None and (fatal_errors or warnings):
        evidence = ClassificationEvidence(
            color_gamma_sxs=None,
            record_mode=None,
            detail="；".join(warnings),
            warnings=tuple(warnings),
        )
        return ScanResult(
            path=video_path,
            mode=ColorMode.ERROR,
            evidence=evidence,
            size=_safe_size(video_path),
            error="；".join(dict.fromkeys(fatal_errors or warnings)),
        )

    color_gamma_sxs: int | None = None
    record_mode: int | None = None
    if packet is not None:
        color_gamma_sxs, record_mode = _read_djmd_values(packet)

    mode, evidence = _classify_evidence(
        color_gamma_sxs=color_gamma_sxs,
        record_mode=record_mode,
        metadata_label=metadata_label,
        warnings=warnings,
    )
    return ScanResult(path=video_path, mode=mode, evidence=evidence, size=_safe_size(video_path))


def _read_djmd_values(packet: bytes) -> tuple[int | None, int | None]:
    """从未知 schema 的 DJI protobuf 中读取当前已验证的两个字段。"""

    fields = parse_proto(packet)
    color_gamma_sxs = value_at_path(fields, [(2, 0), (2, 0), (3, 0)], 1)
    record_mode = value_at_path(fields, [(2, 0), (3, 0)], 5)
    return color_gamma_sxs, record_mode


def _classify_evidence(
    *,
    color_gamma_sxs: int | None,
    record_mode: int | None,
    metadata_label: str | None,
    warnings: list[str] | None = None,
) -> tuple[ColorMode, ClassificationEvidence]:
    """按明确标签、枚举、兼容规则的优先级合并色彩证据。"""

    messages = list(warnings or [])
    label_mode = _mode_from_metadata_label(metadata_label)
    enum_mode = {22: ColorMode.DLOG2, 2: ColorMode.DLOG}.get(color_gamma_sxs)

    if metadata_label is not None and label_mode is None:
        messages.append(f"QuickTime 色彩标签未收录：{metadata_label}")

    if label_mode is not None and enum_mode is not None and label_mode is not enum_mode:
        messages.append(
            f"元数据冲突：QuickTime 标签为 {metadata_label}，djmd 枚举 {color_gamma_sxs} 对应 {enum_mode.label}"
        )
        mode = ColorMode.UNKNOWN
        primary_source = "conflict"
        confidence = "unknown"
    elif label_mode is not None:
        mode = label_mode
        primary_source = "quicktime_mdta"
        confidence = "high"
    elif enum_mode is not None:
        mode = enum_mode
        primary_source = "djmd_gamma_enum"
        confidence = "medium"
    elif color_gamma_sxs is None and record_mode == 8:
        mode = ColorMode.REC709
        primary_source = "djmd_record_mode"
        confidence = "low"
    else:
        mode = ColorMode.UNKNOWN
        primary_source = "unknown"
        confidence = "unknown"

    detail_parts = [
        f"QuickTime ColorGammaSxS={metadata_label}",
        f"djmd: top2.top2.top3.field1={color_gamma_sxs}; top2.top3.field5={record_mode}",
        f"主证据={primary_source}",
        f"置信度={confidence}",
    ]
    if messages:
        detail_parts.append("警告=" + "；".join(messages))
    evidence = ClassificationEvidence(
        color_gamma_sxs=color_gamma_sxs,
        record_mode=record_mode,
        detail="；".join(detail_parts),
        metadata_label=metadata_label,
        primary_source=primary_source,
        confidence=confidence,
        warnings=tuple(messages),
    )
    return mode, evidence


def _mode_from_metadata_label(label: str | None) -> ColorMode | None:
    """规范化已验证的 QuickTime 色彩标签，不对未知文本做模糊推断。"""

    if label is None:
        return None
    return METADATA_LABEL_MODES.get(label.strip().casefold())


def _safe_size(path: Path) -> int:
    """读取文件大小，失败时返回 0，避免错误处理阶段再次抛异常。"""

    try:
        return path.stat().st_size
    except OSError:
        return 0
