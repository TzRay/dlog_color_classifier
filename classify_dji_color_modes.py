#!/usr/bin/env python3
"""按 DJI 私有元数据区分 Osmo Pocket 4P 视频色彩模式。

脚本只读取 MP4 容器元数据和 DJI 的 djmd 数据轨，不解码视频画面内容。
当前映射来自本目录样本的字段对比，并结合 DJI 客服提到的 ColorGammaSxS 线索：

- ColorGammaSxS 枚举 22：D-Log2
- ColorGammaSxS 枚举 2：D-Log
- ColorGammaSxS 缺失且记录模式字段为 8：普通 Rec.709
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ProtoField:
    """未知 schema 的 protobuf 字段。"""

    number: int
    wire_type: int
    value: int | bytes | list["ProtoField"] | None


def run_command(args: list[str], *, text: bool = False) -> subprocess.CompletedProcess:
    """运行外部命令，失败时输出中文错误，便于定位环境问题。"""

    result = subprocess.run(args, capture_output=True, text=text)
    if result.returncode != 0:
        command = " ".join(args)
        stderr = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"命令执行失败：{command}\n{stderr}")
    return result


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """读取 protobuf varint。"""

    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            break
    raise ValueError("无效的 varint 数据")


def parse_proto(data: bytes, *, depth: int = 0, max_depth: int = 8) -> list[ProtoField]:
    """用未知 schema 方式解析 protobuf，保留字段号和值。"""

    fields: list[ProtoField] = []
    offset = 0
    while offset < len(data):
        try:
            key, offset = read_varint(data, offset)
        except ValueError:
            break

        number = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:
            value, offset = read_varint(data, offset)
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 1:
            value = data[offset : offset + 8]
            offset += 8
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            payload = data[offset : offset + length]
            offset += length
            # DJI 的 djmd 是嵌套 protobuf；递归解析能拿到 ColorGammaSxS 对应枚举。
            if depth < max_depth and payload:
                nested = parse_proto(payload, depth=depth + 1, max_depth=max_depth)
                fields.append(ProtoField(number, wire_type, nested))
            else:
                fields.append(ProtoField(number, wire_type, payload))
        elif wire_type == 5:
            value = data[offset : offset + 4]
            offset += 4
            fields.append(ProtoField(number, wire_type, value))
        else:
            break
    return fields


def nested_message(fields: list[ProtoField], number: int, index: int = 0) -> list[ProtoField]:
    """按字段号取得第 index 个嵌套 message。"""

    matches = [
        field.value
        for field in fields
        if field.number == number and field.wire_type == 2 and isinstance(field.value, list)
    ]
    if len(matches) <= index:
        return []
    return matches[index]


def first_int(fields: list[ProtoField], number: int) -> int | None:
    """读取当前层第一个 varint 字段。"""

    for field in fields:
        if field.number == number and field.wire_type == 0 and isinstance(field.value, int):
            return field.value
    return None


def value_at_path(fields: list[ProtoField], path: Iterable[tuple[int, int]], value_field: int) -> int | None:
    """按嵌套路径读取 varint 值。"""

    current = fields
    for number, index in path:
        current = nested_message(current, number, index)
        if not current:
            return None
    return first_int(current, value_field)


def find_djmd_stream_index(video_path: Path) -> int:
    """用 ffprobe 找到 DJI 元数据轨 djmd 的流序号。"""

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_tag_string",
            "-of",
            "json",
            str(video_path),
        ],
        text=True,
    )
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_tag_string") == "djmd":
            return int(stream["index"])
    raise RuntimeError(f"未找到 djmd 数据轨：{video_path.name}")


def read_first_djmd_packet(video_path: Path) -> bytes:
    """抽取 djmd 第一包；这里不读取或分析视频画面。"""

    stream_index = find_djmd_stream_index(video_path)
    result = run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-map",
            f"0:{stream_index}",
            "-c",
            "copy",
            "-frames:d",
            "1",
            "-f",
            "data",
            "-",
        ]
    )
    return result.stdout


def classify_file(video_path: Path) -> dict[str, str | int | None]:
    """识别单个视频的色彩模式。"""

    fields = parse_proto(read_first_djmd_packet(video_path))

    # 这个路径在样本中呈现 2 / 22 / 缺失三态，和官方 ColorGammaSxS 线索吻合。
    color_gamma_sxs = value_at_path(fields, [(2, 0), (2, 0), (3, 0)], 1)
    record_mode = value_at_path(fields, [(2, 0), (3, 0)], 5)

    if color_gamma_sxs == 22:
        mode = "D-Log2"
    elif color_gamma_sxs == 2:
        mode = "D-Log"
    elif color_gamma_sxs is None and record_mode == 8:
        mode = "普通709"
    else:
        mode = "无法确认"

    return {
        "文件名": video_path.name,
        "判定": mode,
        "ColorGammaSxS枚举": color_gamma_sxs,
        "记录模式字段": record_mode,
        "证据": f"djmd: top2.top2.top3.field1={color_gamma_sxs}; top2.top3.field5={record_mode}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="按 DJI djmd 元数据区分 D-Log / D-Log2 / 普通709。")
    parser.add_argument("directory", type=Path, help="包含 DJI MP4 文件的目录")
    parser.add_argument("--output", type=Path, default=Path("dji_color_modes.csv"), help="CSV 输出路径")
    args = parser.parse_args()

    # Windows 路径大小写不敏感，必须按 resolve 后的路径去重，避免 *.MP4 和 *.mp4 重复计数。
    videos = sorted({video.resolve(): video for pattern in ("*.MP4", "*.mp4") for video in args.directory.glob(pattern)}.values())
    if not videos:
        print("未找到 MP4 文件。", file=sys.stderr)
        return 1

    rows = [classify_file(video) for video in videos]
    with args.output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["文件名", "判定", "ColorGammaSxS枚举", "记录模式字段", "证据"])
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["判定"])] = counts.get(str(row["判定"]), 0) + 1
    print(f"已完成识别：{args.output}")
    for mode, count in sorted(counts.items()):
        print(f"{mode}: {count} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
