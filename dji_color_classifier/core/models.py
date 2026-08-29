"""核心数据结构。

这些 dataclass 是 CLI、GUI、测试之间的稳定边界。模块内不做文件操作，
只描述识别结果、整理计划和执行结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ColorMode(str, Enum):
    """视频色彩模式。"""

    DLOG = "dlog"
    DLOG2 = "dlog2"
    REC709 = "rec709"
    REC2100_HLG = "rec2100_hlg"
    UNKNOWN = "unknown"
    ERROR = "error"

    @property
    def label(self) -> str:
        """返回面向用户显示的中文名称。"""

        return {
            ColorMode.DLOG: "D-Log",
            ColorMode.DLOG2: "D-Log2",
            ColorMode.REC709: "普通709",
            ColorMode.REC2100_HLG: "Rec.2100 HLG（HDR）",
            ColorMode.UNKNOWN: "无法确认",
            ColorMode.ERROR: "识别失败",
        }[self]


class PlanAction(str, Enum):
    """文件整理动作。"""

    NONE = "none"
    RENAME = "rename"
    MOVE = "move"
    COPY = "copy"
    DELETE = "delete"


class ConflictPolicy(str, Enum):
    """目标路径冲突处理策略。"""

    ERROR = "error"
    SKIP = "skip"
    SUFFIX = "suffix"


@dataclass(frozen=True)
class ClassificationEvidence:
    """分类证据，方便报告和问题排查。"""

    color_gamma_sxs: int | None
    record_mode: int | None
    reader: str = "native"
    detail: str = ""
    metadata_label: str | None = None
    primary_source: str = "unknown"
    confidence: str = "unknown"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    """单个视频文件的扫描结果。"""

    path: Path
    mode: ColorMode
    evidence: ClassificationEvidence
    size: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """是否成功得到可用识别结果。"""

        return self.error is None and self.mode is not ColorMode.ERROR


@dataclass(frozen=True)
class PlanItem:
    """单个文件的整理计划。"""

    source: Path
    target: Path | None
    action: PlanAction
    scan_result: ScanResult
    skipped: bool = False
    reason: str | None = None


@dataclass
class ExecutionRecord:
    """单个计划项的执行记录。"""

    source: Path
    target: Path | None
    action: PlanAction
    mode: ColorMode
    success: bool
    message: str = ""
    source_size: int | None = None
    target_size: int | None = None


@dataclass
class Manifest:
    """一次执行的操作清单。"""

    version: str
    created_at: str
    root: Path
    operation: str
    records: list[ExecutionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""

        return {
            "version": self.version,
            "created_at": self.created_at,
            "root": str(self.root),
            "operation": self.operation,
            "records": [
                {
                    "source": str(record.source),
                    "target": str(record.target) if record.target else None,
                    "action": record.action.value,
                    "mode": record.mode.value,
                    "success": record.success,
                    "message": record.message,
                    "source_size": record.source_size,
                    "target_size": record.target_size,
                }
                for record in self.records
            ],
        }
