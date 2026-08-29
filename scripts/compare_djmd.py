"""批量对比脚本：汇总 DJI 视频的 djmd 关键字段与帧率。

用于判断“无法确认”素材与已确认 709 / D-Log 素材的结构差异，
例如记录模式字段、gamma 字段是否存在于其他路径、是否携带 LUT 数据等。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dji_color_classifier.core.mp4_reader import (
    _find_box,
    _parse_children,
    read_first_djmd_packet,
)
from dji_color_classifier.core.proto_reader import ProtoField, parse_proto


def collect_varints(fields: list[ProtoField]) -> list[int]:
    """收集树中所有 varint 数值，便于查找 gamma 枚举出现在哪些路径。"""

    values: list[int] = []
    for field in fields:
        if isinstance(field.value, int):
            values.append(field.value)
        elif isinstance(field.value, list):
            values.extend(collect_varints(field.value))
    return values


def field_inventory(fields: list[ProtoField]) -> dict[int, int]:
    """统计当前层每个字段号出现的次数。"""

    counts: dict[int, int] = {}
    for field in fields:
        counts[field.number] = counts.get(field.number, 0) + 1
    return counts


def nested(fields: list[ProtoField], number: int) -> list[ProtoField]:
    """取当前层第 0 个嵌套 message。"""

    for field in fields:
        if field.number == number and field.wire_type == 2 and isinstance(field.value, list):
            return field.value
    return []


def probe_frame_rate(video_path: Path) -> dict:
    """读取视频轨 mdhd timescale、stts 全表和 stsz 总数，计算首段与平均帧率。"""

    with video_path.open("rb") as handle:
        file_size = video_path.stat().st_size
        top_boxes = _parse_children(handle, 0, file_size)
        moov = _find_box(top_boxes, ("moov",))
        if moov is None:
            return {"fps": None, "avg_fps": None}
        for trak in [box for box in moov.children if box.type == "trak"]:
            mdia = _find_box([trak], ("trak", "mdia"))
            if mdia is None:
                continue
            mdhd = _find_box([mdia], ("mdia", "mdhd"))
            if mdhd is None:
                continue
            handle.seek(mdhd.payload_start)
            payload = handle.read(mdhd.size - mdhd.header_size)
            version = payload[0]
            if version == 0:
                timescale = struct.unpack_from(">I", payload, 12)[0]
            else:
                timescale = struct.unpack_from(">I", payload, 20)[0]
            stbl = _find_box([mdia], ("mdia", "minf", "stbl"))
            stts = None
            stsz = None
            if stbl is not None:
                for child in stbl.children:
                    if child.type == "stts":
                        stts = child
                    elif child.type in {"stsz", "stz2"}:
                        stsz = child
            first_delta = None
            total_delta = 0
            sample_count = 0
            if stts is not None:
                handle.seek(stts.payload_start)
                stts_payload = handle.read(stts.size - stts.header_size)
                entry_count = struct.unpack_from(">I", stts_payload, 4)[0]
                offset = 8
                for _ in range(entry_count):
                    count, delta = struct.unpack_from(">II", stts_payload, offset)
                    offset += 8
                    if first_delta is None:
                        first_delta = delta
                    total_delta += count * delta
                    sample_count += count
            if stsz is not None:
                handle.seek(stsz.payload_start)
                stsz_payload = handle.read(stsz.size - stsz.header_size)
                if stsz.type == "stsz" and len(stsz_payload) >= 12:
                    sample_size, stsz_count = struct.unpack_from(">II", stsz_payload, 4)
                    if stsz_count:
                        sample_count = stsz_count
                elif stsz.type == "stz2" and len(stsz_payload) >= 12:
                    sample_count = struct.unpack_from(">I", stsz_payload, 8)[0]
            fps = round(timescale / first_delta, 2) if first_delta else None
            avg_fps = round(timescale * sample_count / total_delta, 2) if total_delta else None
            return {"fps": fps, "avg_fps": avg_fps}
    return {"fps": None, "avg_fps": None}


def probe_resolution(video_path: Path) -> dict:
    """读取视频轨 stsd 首个 sample entry 的宽高。"""

    with video_path.open("rb") as handle:
        file_size = video_path.stat().st_size
        top_boxes = _parse_children(handle, 0, file_size)
        moov = _find_box(top_boxes, ("moov",))
        if moov is None:
            return {"width": None, "height": None}
        for trak in [box for box in moov.children if box.type == "trak"]:
            stbl = _find_box([trak], ("trak", "mdia", "minf", "stbl"))
            if stbl is None:
                continue
            stsd = None
            for child in stbl.children:
                if child.type == "stsd":
                    stsd = child
                    break
            if stsd is None:
                continue
            handle.seek(stsd.payload_start)
            payload = handle.read(stsd.size - stsd.header_size)
            if len(payload) < 16:
                continue
            entry_size, entry_type = struct.unpack_from(">I4s", payload, 8)
            if entry_size >= 40:
                width, height = struct.unpack_from(">HH", payload, 40)
                return {"width": width, "height": height}
        return {"width": None, "height": None}


def analyze(path: Path) -> dict:
    """汇总单个视频的关键字段。"""

    packet = read_first_djmd_packet(path)
    fields = parse_proto(packet)
    top2 = nested(fields, 2)
    top2_2 = nested(top2, 2)  # 相机/固件信息消息
    top2_3 = nested(top2, 3)  # 视频信息消息（含记录模式字段）
    top3 = nested(fields, 3)
    top3_2 = nested(top3, 2)  # 拍摄参数消息（gamma 常规路径的上一级）
    top3_2_3 = nested(top3_2, 3)

    def first_int(items: list[ProtoField], number: int) -> int | None:
        for item in items:
            if item.number == number and item.wire_type == 0 and isinstance(item.value, int):
                return item.value
        return None

    # 统计 LUT 疑似字段（top3.field35）中的浮点条目数量
    lut_entries = 0
    for item in top3:
        if item.number == 35 and isinstance(item.value, list):
            lut_entries = len(item.value)

    return {
        "file": path.name,
        "packet_bytes": len(packet),
        "record_mode": first_int(top2_3, 5),
        "gamma_at_path": first_int(top3_2_3, 1),
        "gamma_anywhere": [v for v in (2, 22) if v in collect_varints(fields)],
        "top2_inventory": field_inventory(top2),
        "top2_2_inventory": field_inventory(top2_2),
        "top2_3_inventory": field_inventory(top2_3),
        "top3_2_inventory": field_inventory(top3_2),
        "top3_2_3_inventory": field_inventory(top3_2_3),
        "lut_field35_entries": lut_entries,
        **probe_frame_rate(path),
        **probe_resolution(path),
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：批量分析目录或单文件并输出 JSON。"""

    parser = argparse.ArgumentParser(description="批量对比 djmd 关键字段")
    parser.add_argument("paths", nargs="+", type=Path, help="目录或 MP4/MOV 文件")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    files: list[Path] = []
    for path in args.paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.mp4") if p.is_file()))
        elif path.is_file():
            files.append(path)

    results = []
    for path in files:
        try:
            results.append(analyze(path))
        except Exception as exc:
            results.append({"file": path.name, "error": str(exc)})

    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
