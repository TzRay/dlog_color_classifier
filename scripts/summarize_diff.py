"""输出分组对比 JSON 的精简摘要，便于快速查看差异结论。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """命令行入口：读取 diff_djmd_groups.py 的 JSON 并打印摘要。"""

    parser = argparse.ArgumentParser(description="精简输出分组对比结果")
    parser.add_argument("diff_json", type=Path, help="diff_djmd_groups.py 输出的 JSON")
    args = parser.parse_args(argv)

    data = json.loads(args.diff_json.read_text(encoding="utf-8"))
    for key, value in data.items():
        if key == "comparisons":
            continue
        print(f"[组] {key}: 文件数={value['file_count']}, 公共路径数={value['common_path_count']}")
        if value["value_diffs"]:
            print(f"  组内值不一致字段: {', '.join(item['path'] for item in value['value_diffs'][:20])}")

    print("\n[对比]")
    for item in data.get("comparisons", []):
        print(f"未知组 {item['unknown_group']} vs 参考组 {item['reference']}")
        print(f"  未知组独有路径: {item['extra_in_unknown']}")
        print(f"  未知组缺失路径: {item['missing_in_unknown']}")
        if item["shared_value_diffs"]:
            print(f"  共享路径值差异: {[d['path'] + '=' + str(d['values']) for d in item['shared_value_diffs'][:15]]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
