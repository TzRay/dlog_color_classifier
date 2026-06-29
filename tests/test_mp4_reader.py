"""原生 MP4 reader 测试。"""

from __future__ import annotations

import struct
from pathlib import Path

from dji_color_classifier.core.mp4_reader import read_first_djmd_packet


def box(box_type: str, payload: bytes) -> bytes:
    """构造普通 32 位 size box。"""

    return struct.pack(">I4s", len(payload) + 8, box_type.encode("latin1")) + payload


def full_box_payload(payload: bytes = b"") -> bytes:
    """构造 version/flags + payload。"""

    return b"\x00\x00\x00\x00" + payload


def build_minimal_djmd_mp4(sample: bytes) -> bytes:
    """构造只包含一个 djmd sample 的最小 MP4。"""

    ftyp = box("ftyp", b"isom\x00\x00\x02\x00isom")
    mdat = box("mdat", sample)
    sample_offset = len(ftyp) + 8

    sample_entry = struct.pack(">I4s", 8, b"djmd")
    stsd = box("stsd", full_box_payload(struct.pack(">I", 1) + sample_entry))
    stsz = box("stsz", full_box_payload(struct.pack(">II", 0, 1) + struct.pack(">I", len(sample))))
    stsc = box("stsc", full_box_payload(struct.pack(">I", 1) + struct.pack(">III", 1, 1, 1)))
    stco = box("stco", full_box_payload(struct.pack(">I", 1) + struct.pack(">I", sample_offset)))
    stbl = box("stbl", stsd + stsz + stsc + stco)
    minf = box("minf", stbl)
    mdia = box("mdia", minf)
    trak = box("trak", mdia)
    moov = box("moov", trak)
    return ftyp + mdat + moov


def test_reads_first_djmd_packet(tmp_path: Path) -> None:
    """reader 应能从 sample table 定位 djmd 第一包。"""

    sample = b"\x12\x03abc"
    path = tmp_path / "sample.mp4"
    path.write_bytes(build_minimal_djmd_mp4(sample))

    assert read_first_djmd_packet(path) == sample
