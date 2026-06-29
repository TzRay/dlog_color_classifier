"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dji_color_classifier import __version__
from dji_color_classifier.core.executor import build_undo_plan, execute_plan
from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import ColorMode, ConflictPolicy
from dji_color_classifier.core.planner import build_plan
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import scan_directory, summarize_results


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("用户中断操作。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(prog="dji-color", description="DJI 视频色彩模式识别与整理工具。")
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="扫描目录并导出识别报告")
    scan.add_argument("directory", type=Path, help="扫描目录")
    scan.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    scan.add_argument("--output", type=Path, default=Path("dji_color_modes.csv"), help="报告输出路径")
    scan.add_argument("--format", choices=["csv", "json"], default="csv", help="报告格式")
    scan.add_argument(
        "--include-unknown",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="报告中包含无法确认文件，默认包含；可用 --no-include-unknown 排除",
    )
    scan.add_argument("--quiet", action="store_true", help="减少输出")
    scan.add_argument("--verbose", action="store_true", help="输出详细诊断")
    scan.set_defaults(handler=handle_scan)

    organize = subparsers.add_parser("organize", help="生成或执行整理计划")
    organize.add_argument("directory", type=Path, help="扫描目录")
    organize.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    organize.add_argument("--mode", choices=["prefix", "move", "copy"], default="prefix", help="整理模式")
    organize.add_argument("--apply", action="store_true", help="真正执行文件修改；默认只预演")
    organize.add_argument("--name-template", help="文件名模板，例如 {mode}_{original}")
    organize.add_argument("--dir-template", help="目录模板，例如 {mode}")
    organize.add_argument("--on-conflict", choices=["error", "skip", "suffix"], default="error", help="目标冲突策略")
    organize.add_argument("--manifest", type=Path, help="manifest 输出路径")
    organize.add_argument("--manifest-dir", type=Path, help="manifest 输出目录")
    organize.add_argument("--report", type=Path, help="同时输出扫描报告")
    organize.add_argument("--format", choices=["csv", "json"], default="csv", help="报告格式")
    organize.add_argument("--with-sidecars", action="store_true", help="同时处理同 basename 的 SRT/LRF/THM 等伴随文件")
    organize.add_argument("--quiet", action="store_true", help="减少输出")
    organize.add_argument("--verbose", action="store_true", help="输出详细诊断")
    organize.set_defaults(handler=handle_organize)

    undo = subparsers.add_parser("undo", help="根据 manifest 撤销上一次操作")
    undo.add_argument("manifest", type=Path, help="manifest 路径")
    undo.add_argument("--apply", action="store_true", help="真正执行撤销；默认只预演")
    undo.add_argument("--on-missing", choices=["skip", "error"], default="error", help="撤销源文件缺失时的策略")
    undo.set_defaults(handler=handle_undo)

    version = subparsers.add_parser("version", help="显示版本")
    version.set_defaults(handler=handle_version)
    return parser


def handle_scan(args: argparse.Namespace) -> int:
    """执行 scan 子命令。"""

    directory = _require_directory(args.directory)
    results = scan_directory(directory, recursive=args.recursive)
    if not args.include_unknown:
        results = [result for result in results if result.mode is not ColorMode.UNKNOWN]
    write_report(results, args.output, fmt=args.format)
    if not args.quiet:
        _print_summary(results)
        print(f"已写入报告：{args.output}")
        if args.verbose:
            _print_errors(results)
    return 0 if results else 1


def handle_organize(args: argparse.Namespace) -> int:
    """执行 organize 子命令。"""

    directory = _require_directory(args.directory)
    results = scan_directory(directory, recursive=args.recursive)
    if args.report:
        write_report(results, args.report, fmt=args.format)

    plan = build_plan(
        results,
        root=directory,
        mode=args.mode,
        conflict_policy=ConflictPolicy(args.on_conflict),
        name_template=args.name_template,
        dir_template=args.dir_template,
        with_sidecars=args.with_sidecars,
    )
    if not args.quiet:
        _print_plan(plan)

    records = execute_plan(plan, apply=args.apply)
    manifest = create_manifest(directory, args.mode, records)
    manifest_path = write_manifest(manifest, _resolve_manifest_path(args.manifest, args.manifest_dir, manifest.created_at))
    if not args.quiet:
        print(f"已写入 manifest：{manifest_path}")
    if not args.apply and not args.quiet:
        print("当前是预演模式，未修改任何文件。确认无误后加 --apply 执行。")
    if args.verbose:
        _print_failed_records(records)
    return 0


def handle_undo(args: argparse.Namespace) -> int:
    """执行 undo 子命令。"""

    manifest = read_manifest(args.manifest)
    plan = build_undo_plan(manifest.records)
    if args.on_missing == "skip":
        plan = [item for item in plan if item.source.exists()]
    else:
        missing = [item.source for item in plan if not item.source.exists()]
        if missing:
            raise FileNotFoundError(f"撤销源文件不存在：{missing[0]}")
    _print_plan(plan)
    records = execute_plan(plan, apply=args.apply)
    undo_manifest = create_manifest(manifest.root, "undo", records)
    path = write_manifest(undo_manifest)
    print(f"已写入撤销 manifest：{path}")
    if not args.apply:
        print("当前是撤销预演模式，未修改任何文件。确认无误后加 --apply 执行。")
    return 0


def handle_version(_args: argparse.Namespace) -> int:
    """显示版本。"""

    print(__version__)
    return 0


def _require_directory(path: Path) -> Path:
    """检查目录存在并返回绝对路径。"""

    directory = path.resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"目录不存在：{directory}")
    return directory


def _print_summary(results: list) -> None:
    """打印识别统计。"""

    print("识别统计：")
    for label, count in sorted(summarize_results(results).items()):
        print(f"  {label}: {count} 个")


def _print_errors(results: list) -> None:
    """输出失败文件详情。"""

    for result in results:
        if result.error:
            print(f"  错误：{result.path} -> {result.error}", file=sys.stderr)


def _print_failed_records(records: list) -> None:
    """输出执行失败记录。"""

    for record in records:
        if not record.success:
            print(f"  执行失败：{record.source} -> {record.message}", file=sys.stderr)


def _resolve_manifest_path(manifest_path: Path | None, manifest_dir: Path | None, created_at: str) -> Path | None:
    """解析 manifest 输出路径。"""

    if manifest_path is not None:
        return manifest_path
    if manifest_dir is None:
        return None
    safe_time = created_at.replace(":", "").replace("-", "")
    return manifest_dir / f"{safe_time}.json"


def _print_plan(plan: list) -> None:
    """打印整理计划。"""

    if not plan:
        print("没有可处理的文件。")
        return
    print("整理计划：")
    for item in plan:
        if item.skipped or item.target is None:
            print(f"  跳过：{item.source.name}，{item.reason or item.scan_result.mode.label}")
        else:
            print(f"  {item.action.value}: {item.source.name} -> {item.target}")


if __name__ == "__main__":
    raise SystemExit(main())
