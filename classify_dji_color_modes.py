#!/usr/bin/env python3
"""兼容旧入口：批量识别 DJI 视频色彩模式并输出 CSV。

新实现已经迁入 `dji_color_classifier` 包，本文件保留旧命令用法，便于老用户平滑迁移。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dji_color_classifier.core.classifier import classify_file as classify_video_file
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import scan_directory, summarize_results


def classify_file(video_path: Path) -> dict[str, str | int | None]:
    """保留旧脚本的字典返回格式。"""

    result = classify_video_file(video_path)
    return {
        "文件名": result.path.name,
        "判定": result.mode.label,
        "ColorGammaSxS枚举": result.evidence.color_gamma_sxs,
        "记录模式字段": result.evidence.record_mode,
        "证据": result.evidence.detail,
    }


def main() -> int:
    """旧 CLI 入口。"""

    parser = argparse.ArgumentParser(description="按 DJI djmd 元数据区分 D-Log / D-Log2 / 普通709。")
    parser.add_argument("directory", type=Path, help="包含 DJI 视频文件的目录")
    parser.add_argument("--output", type=Path, default=Path("dji_color_modes.csv"), help="CSV 输出路径")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"目录不存在：{directory}", file=sys.stderr)
        return 1

    results = scan_directory(directory, recursive=args.recursive)
    if not results:
        print("未找到视频文件。", file=sys.stderr)
        return 1

    write_report(results, args.output, fmt="csv")
    print(f"已完成识别：{args.output}")
    for mode, count in sorted(summarize_results(results).items()):
        print(f"{mode}: {count} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
