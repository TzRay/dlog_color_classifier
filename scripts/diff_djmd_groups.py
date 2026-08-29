"""分组对比脚本：按判定结果分组，输出各组 djmd 字段的结构差异。

用于回答“无法确认素材与普通 709 / D-Log 素材有什么区别”，
做法是把每个文件的 protobuf 字段展开成“路径 -> 值”映射，
再对比不同分组的公共路径、独有路径与值差异。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dji_color_classifier.core.mp4_reader import read_first_djmd_packet
from dji_color_classifier.core.proto_reader import ProtoField, parse_proto


def walk(fields: list[ProtoField], prefix: str) -> dict[str, object]:
    """把字段树展平成“路径 -> 值”字典，路径形如 2/2/3/1。"""

    out: dict[str, object] = {}
    for index, field in enumerate(fields):
        key = f"{prefix}{field.number}" if not prefix else f"{prefix}/{field.number}"
        if isinstance(field.value, list):
            out[key] = None  # message：只记录存在
            out.update(walk(field.value, key))
        elif isinstance(field.value, int):
            out[key] = field.value
        elif isinstance(field.value, bytes):
            out[key] = field.value.hex()
        else:
            out[key] = None
    return out


def common_paths(rows: list[dict[str, object]]) -> set[str]:
    """返回所有文件都存在的路径集合。"""

    if not rows:
        return set()
    result = set(rows[0])
    for row in rows[1:]:
        result &= set(row)
    return result


def value_diffs(rows: list[dict[str, object]], paths: set[str]) -> list[dict]:
    """列出在公共路径上取值不一致的字段及其各取值计数。"""

    diffs = []
    for path in sorted(paths):
        values = [row.get(path) for row in rows]
        distinct = {repr(value) for value in values}
        if len(distinct) > 1:
            counts: dict[str, int] = {}
            for value in values:
                counts[repr(value)] = counts.get(repr(value), 0) + 1
            diffs.append({"path": path, "values": counts})
    return diffs


def summarize(paths: set[str], prefix: str = "") -> list[str]:
    """打印路径集合，便于人工检查（限制条数避免刷屏）。"""

    items = sorted(paths)
    if len(items) <= 80:
        return items
    return items[:40] + [f"... 共 {len(items)} 项"] + items[-40:]


def main(argv: list[str] | None = None) -> int:
    """命令行入口：分析目录，按现有判定规则分组输出 JSON。"""

    parser = argparse.ArgumentParser(description="分组对比 djmd 字段结构")
    parser.add_argument("paths", nargs="+", type=Path, help="目录或 MP4/MOV 文件")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    files: list[Path] = []
    for path in args.paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.mp4") if p.is_file()))
        elif path.is_file():
            files.append(path)

    groups: dict[str, list[dict[str, object]]] = {}
    for path in files:
        packet = read_first_djmd_packet(path)
        fields = parse_proto(packet)
        row = walk(fields, "")

        top2 = next((f.value for f in fields if f.number == 2 and isinstance(f.value, list)), [])
        top2_3 = next((f.value for f in top2 if f.number == 3 and isinstance(f.value, list)), [])
        record_mode = next(
            (f.value for f in top2_3 if f.number == 5 and f.wire_type == 0 and isinstance(f.value, int)),
            None,
        )
        top2_2 = next((f.value for f in top2 if f.number == 2 and isinstance(f.value, list)), [])
        top2_2_3 = next((f.value for f in top2_2 if f.number == 3 and isinstance(f.value, list)), [])
        gamma = next(
            (f.value for f in top2_2_3 if f.number == 1 and f.wire_type == 0 and isinstance(f.value, int)),
            None,
        )

        if gamma == 22:
            label = "D-Log2"
        elif gamma == 2:
            label = "D-Log"
        elif gamma is None and record_mode == 8:
            label = "普通709"
        else:
            label = "未知"
        group_key = f"{label}:{path.parent.name}" if label == "未知" else label
        groups.setdefault(group_key, []).append(row)

    result = {}
    for name, rows in sorted(groups.items()):
        common = common_paths(rows)
        result[name] = {
            "file_count": len(rows),
            "common_path_count": len(common),
            "common_paths": summarize(common),
            "value_diffs": value_diffs(rows, common),
        }

    # 额外对比：未知组之间、未知组与 709 / D-Log 之间
    def group(name: str):
        return groups[name]

    comparisons = []
    unknown_keys = [name for name in groups if name.startswith("未知")]
    refs = {
        "普通709": "普通709",
        "D-Log": "D-Log",
    }
    for unk in unknown_keys:
        for ref_name, ref_key in refs.items():
            if ref_key not in groups:
                continue
            unk_rows, ref_rows = group(unk), group(ref_key)
            unk_common = common_paths(unk_rows)
            ref_common = common_paths(ref_rows)
            ref_union = set().union(*ref_rows) if ref_rows else set()
            unk_union = set().union(*unk_rows) if unk_rows else set()
            # 严格口径：未知组全部文件都有、参考组任何文件都没有的路径
            extra = unk_common - ref_union
            # 严格口径：参考组全部文件都有、未知组任何文件都没有的路径
            missing = ref_common - unk_union
            comparisons.append(
                {
                    "unknown_group": unk,
                    "reference": ref_name,
                    "extra_in_unknown": summarize(extra),
                    "missing_in_unknown": summarize(missing),
                    "shared_value_diffs": value_diffs(unk_rows + ref_rows, unk_common & ref_common),
                }
            )
    result["comparisons"] = comparisons

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
