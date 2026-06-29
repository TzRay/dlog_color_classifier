"""DJI `djmd` 元数据色彩模式判定。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
from dji_color_classifier.core.mp4_reader import read_first_djmd_packet
from dji_color_classifier.core.proto_reader import parse_proto, value_at_path


def classify_djmd_packet(packet: bytes) -> tuple[ColorMode, ClassificationEvidence]:
    """根据 `djmd` 第一包内容判定色彩模式。"""

    fields = parse_proto(packet)
    color_gamma_sxs = value_at_path(fields, [(2, 0), (2, 0), (3, 0)], 1)
    record_mode = value_at_path(fields, [(2, 0), (3, 0)], 5)

    if color_gamma_sxs == 22:
        mode = ColorMode.DLOG2
    elif color_gamma_sxs == 2:
        mode = ColorMode.DLOG
    elif color_gamma_sxs is None and record_mode == 8:
        mode = ColorMode.REC709
    else:
        mode = ColorMode.UNKNOWN

    evidence = ClassificationEvidence(
        color_gamma_sxs=color_gamma_sxs,
        record_mode=record_mode,
        detail=f"djmd: top2.top2.top3.field1={color_gamma_sxs}; top2.top3.field5={record_mode}",
    )
    return mode, evidence


def classify_file(video_path: Path) -> ScanResult:
    """识别单个 DJI 视频文件。"""

    try:
        packet = read_first_djmd_packet(video_path)
        mode, evidence = classify_djmd_packet(packet)
        return ScanResult(path=video_path, mode=mode, evidence=evidence, size=video_path.stat().st_size)
    except Exception as exc:
        # 批处理阶段会继续处理其他文件，因此这里将异常降级为结果对象。
        evidence = ClassificationEvidence(color_gamma_sxs=None, record_mode=None, detail="")
        return ScanResult(path=video_path, mode=ColorMode.ERROR, evidence=evidence, size=_safe_size(video_path), error=str(exc))


def _safe_size(path: Path) -> int:
    """读取文件大小，失败时返回 0，避免错误处理阶段再次抛异常。"""

    try:
        return path.stat().st_size
    except OSError:
        return 0
