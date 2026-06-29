#!/usr/bin/env python3
"""兼容旧入口：给 D-Log / D-Log2 视频添加文件名前缀。

新实现已经迁入 `dji_color_classifier` 包。本文件保留原有预演和 `--apply` 用法。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dji_color_classifier.core.executor import execute_plan
from dji_color_classifier.core.manifest import create_manifest, write_manifest
from dji_color_classifier.core.models import ConflictPolicy
from dji_color_classifier.core.planner import build_plan
from dji_color_classifier.core.scanner import scan_directory


def main() -> int:
    """旧 CLI 入口。"""

    parser = argparse.ArgumentParser(description="给 DJI 视频添加 D-Log / D-Log2 文件名前缀。")
    parser.add_argument("directory", type=Path, help="需要处理的视频目录")
    parser.add_argument("--apply", action="store_true", help="真正执行重命名；默认只预演")
    parser.add_argument("--separator", default="_", help="前缀和原文件名之间的分隔符，默认是下划线")
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"目录不存在：{directory}", file=sys.stderr)
        return 1

    name_template = "{mode}" + args.separator + "{original}"
    results = [result for result in scan_directory(directory) if result.path.name.upper().startswith("DJI_")]
    plan = build_plan(
        results,
        root=directory,
        mode="prefix",
        conflict_policy=ConflictPolicy.ERROR,
        name_template=name_template,
    )

    if not plan:
        print("没有需要重命名的文件。")
        return 0

    print("\n重命名计划：")
    for item in plan:
        if item.skipped or item.target is None:
            print(f"跳过：{item.source.name}，{item.reason or item.scan_result.mode.label}")
        else:
            print(f"{item.scan_result.mode.label}: {item.source.name} -> {item.target.name}")

    records = execute_plan(plan, apply=args.apply)
    manifest = create_manifest(directory, "prefix", records)
    manifest_path = write_manifest(manifest)
    print(f"\n已写入 manifest：{manifest_path}")

    if not args.apply:
        print("当前是预演模式，未修改任何文件。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
