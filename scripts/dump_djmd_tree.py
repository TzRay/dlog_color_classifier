"""诊断脚本：导出 DJI `djmd` 元数据树与视频轨帧率信息。

用途：对比“无法确认”素材与已确认的 709 / D-Log 素材，
找出 `ColorGammaSxS` 缺失时的结构差异，以及是否属于高帧率（慢动作）录制。
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


def simplify(fields: list[ProtoField]) -> list[list]:
    """把 protobuf 字段树转成可 JSON 序列化的结构，保留重复字段与顺序。"""

    out: list[list] = []
    for field in fields:
        if isinstance(field.value, list):
            out.append([field.number, simplify(field.value)])
        elif isinstance(field.value, int):
            out.append([field.number, field.value])
        elif isinstance(field.value, bytes):
            out.append([field.number, field.value.hex()])
        else:
            out.append([field.number, None])
    return out


def probe_frame_rate(video_path: Path) -> dict:
    """读取视频轨 mdhd timescale 与 stts 首条采样间隔，估算帧率。"""

    with video_path.open("rb") as handle:
        file_size = video_path.stat().st_size
        top_boxes = _parse_children(handle, 0, file_size)
        moov = _find_box(top_boxes, ("moov",))
        if moov is None:
            return {"error": "未找到 moov"}
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
            if stbl is not None:
                for child in stbl.children:
                    if child.type == "stts":
                        stts = child
                        break
            delta = None
            if stts is not None:
                handle.seek(stts.payload_start)
                stts_payload = handle.read(stts.size - stts.header_size)
                if len(stts_payload) >= 16:
                    _count, delta = struct.unpack_from(">II", stts_payload, 8)
            fps = round(timescale / delta, 2) if delta else None
            return {"timescale": timescale, "stts_delta": delta, "fps": fps}
    return {"error": "未找到视频轨"}


def dump_file(path: Path) -> dict:
    """导出单个视频的 djmd 树、判定字段与帧率信息。"""

    packet = read_first_djmd_packet(path)
    fields = parse_proto(packet)
    frame_info = probe_frame_rate(path)
    return {
        "file": path.name,
        "packet_bytes": len(packet),
        "djmd_tree": simplify(fields),
        "frame_info": frame_info,
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：对每个文件导出 JSON 诊断信息。"""

    parser = argparse.ArgumentParser(description="导出 djmd 树与帧率诊断信息")
    parser.add_argument("paths", nargs="+", type=Path, help="MP4/MOV 文件路径")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    results = []
    for path in args.paths:
        try:
            results.append(dump_file(path))
        except Exception as exc:  # 单个文件失败不中断整体诊断
            results.append({"file": path.name, "error": str(exc)})

    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
