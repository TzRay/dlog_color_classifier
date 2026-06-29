#!/usr/bin/env python3
"""按 DJI 私有元数据给 D-Log / D-Log2 视频添加文件名前缀。

只处理目录内以 DJI_ 开头的视频文件：
- D-Log  -> 文件名前加 dlog_
- D-Log2 -> 文件名前加 dlog2_
- 普通 709 或无法确认 -> 不改名

默认只预演并打印计划；加 --apply 才会真正重命名。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from classify_dji_color_modes import classify_file


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


def iter_dji_videos(directory: Path) -> list[Path]:
    """列出目录中以 DJI_ 开头的视频文件，并兼容 Windows 大小写不敏感路径。"""

    videos: dict[Path, Path] = {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if not path.name.upper().startswith("DJI_"):
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        videos[path.resolve()] = path
    return sorted(videos.values(), key=lambda item: item.name.lower())


def build_target_name(path: Path, mode: str, separator: str) -> str | None:
    """根据识别结果生成目标文件名；709 和无法确认不改名。"""

    if mode == "D-Log":
        prefix = f"dlog{separator}"
    elif mode == "D-Log2":
        prefix = f"dlog2{separator}"
    else:
        # 用户要求无法确认也按普通 709 处理，因此这里不添加任何前缀。
        return None

    lower_name = path.name.lower()
    if lower_name.startswith("dlog_") or lower_name.startswith("dlog2_") or lower_name.startswith("dlog") or lower_name.startswith("dlog2"):
        return None
    return f"{prefix}{path.name}"


def collect_rename_plan(directory: Path, separator: str) -> list[tuple[Path, Path, str]]:
    """生成重命名计划，并在发现目标冲突时直接报错。"""

    plan: list[tuple[Path, Path, str]] = []
    for video in iter_dji_videos(directory):
        result = classify_file(video)
        mode = str(result["判定"])
        target_name = build_target_name(video, mode, separator)
        if target_name is None:
            print(f"跳过：{video.name}，识别为 {mode}，无需改名")
            continue

        target = video.with_name(target_name)
        if target.exists():
            raise FileExistsError(f"目标文件已存在，停止执行：{target}")
        plan.append((video, target, mode))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="给 DJI_ 开头的 D-Log / D-Log2 视频添加文件名前缀。")
    parser.add_argument("directory", type=Path, help="需要处理的视频目录")
    parser.add_argument("--apply", action="store_true", help="真正执行重命名；默认只预演")
    parser.add_argument("--separator", default="_", help="前缀和原文件名之间的分隔符，默认是下划线")
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"目录不存在：{directory}", file=sys.stderr)
        return 1

    try:
        plan = collect_rename_plan(directory, args.separator)
    except Exception as exc:
        print(f"生成重命名计划失败：{exc}", file=sys.stderr)
        return 1

    if not plan:
        print("没有需要重命名的文件。")
        return 0

    print("\n重命名计划：")
    for source, target, mode in plan:
        print(f"{mode}: {source.name} -> {target.name}")

    if not args.apply:
        print("\n当前是预演模式，未修改任何文件。确认无误后加 --apply 执行。")
        return 0

    for source, target, mode in plan:
        source.rename(target)
        print(f"已重命名：{source.name} -> {target.name}（{mode}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
