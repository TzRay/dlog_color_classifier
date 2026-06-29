"""扫描报告导出。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from dji_color_classifier.core.models import ScanResult


REPORT_FIELDS = ["文件名", "路径", "判定", "ColorGammaSxS枚举", "记录模式字段", "文件大小", "证据", "错误"]


def write_report(results: list[ScanResult], output: Path, *, fmt: str = "csv") -> None:
    """按指定格式写入扫描报告。"""

    output.parent.mkdir(parents=True, exist_ok=True) if output.parent != Path(".") else None
    if fmt == "csv":
        _write_csv(results, output)
    elif fmt == "json":
        _write_json(results, output)
    else:
        raise ValueError(f"不支持的报告格式：{fmt}")


def _write_csv(results: list[ScanResult], output: Path) -> None:
    """写入 CSV 报告。"""

    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(_result_to_row(result))


def _write_json(results: list[ScanResult], output: Path) -> None:
    """写入 JSON 报告。"""

    output.write_text(
        json.dumps([_result_to_row(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _result_to_row(result: ScanResult) -> dict[str, str | int | None]:
    """将识别结果转换为报告行。"""

    return {
        "文件名": result.path.name,
        "路径": str(result.path),
        "判定": result.mode.label,
        "ColorGammaSxS枚举": result.evidence.color_gamma_sxs,
        "记录模式字段": result.evidence.record_mode,
        "文件大小": result.size,
        "证据": result.evidence.detail,
        "错误": result.error,
    }
