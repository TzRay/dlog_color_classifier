"""原生 MP4 reader 测试。"""

from __future__ import annotations

import struct
import tracemalloc
from pathlib import Path

import pytest

from dji_color_classifier.core import mp4_reader
from dji_color_classifier.core.mp4_reader import Mp4ReaderError, read_first_djmd_packet


def box(box_type: str, payload: bytes) -> bytes:
    """构造普通 32 位 size box。"""

    return struct.pack(">I4s", len(payload) + 8, box_type.encode("latin1")) + payload


def full_box_payload(payload: bytes = b"") -> bytes:
    """构造 version/flags + payload。"""

    return b"\x00\x00\x00\x00" + payload


def build_minimal_djmd_mp4(
    sample: bytes,
    *,
    compact_bits: int | None = None,
    use_co64: bool = False,
    constant_count: int | None = None,
    extra_traks: bytes = b"",
) -> bytes:
    """构造只包含一个 djmd sample 的最小 MP4。"""

    ftyp = box("ftyp", b"isom\x00\x00\x02\x00isom")
    mdat = box("mdat", sample)
    sample_offset = len(ftyp) + 8

    sample_entry = struct.pack(">I4s", 8, b"djmd")
    stsd = box("stsd", full_box_payload(struct.pack(">I", 1) + sample_entry))
    stsz = box("stsz", full_box_payload(struct.pack(">II", 0, 1) + struct.pack(">I", len(sample))))
    if constant_count is not None:
        stsz = box("stsz", full_box_payload(struct.pack(">II", len(sample), constant_count)))
    elif compact_bits is not None:
        raw_size = bytes([len(sample) << 4]) if compact_bits == 4 else len(sample).to_bytes(compact_bits // 8, "big")
        stsz = box("stz2", full_box_payload(b"\x00\x00\x00" + bytes([compact_bits]) + struct.pack(">I", 1) + raw_size))
    stsc = box("stsc", full_box_payload(struct.pack(">I", 1) + struct.pack(">III", 1, 1, 1)))
    stco = box("stco", full_box_payload(struct.pack(">I", 1) + struct.pack(">I", sample_offset)))
    if use_co64:
        stco = box("co64", full_box_payload(struct.pack(">I", 1) + struct.pack(">Q", sample_offset)))
    stbl = box("stbl", stsd + stsz + stsc + stco)
    minf = box("minf", stbl)
    mdia = box("mdia", minf)
    trak = box("trak", mdia)
    moov = box("moov", extra_traks + trak)
    return ftyp + mdat + moov


def test_reads_first_djmd_packet(tmp_path: Path) -> None:
    """reader 应能从 sample table 定位 djmd 第一包。"""

    sample = b"\x12\x03abc"
    path = tmp_path / "sample.mp4"
    path.write_bytes(build_minimal_djmd_mp4(sample))

    assert read_first_djmd_packet(path) == sample


@pytest.mark.parametrize("compact_bits", [4, 8, 16])
def test_compact_sizes_and_co64(tmp_path: Path, compact_bits: int) -> None:
    """首包定点读取同时支持 compact sizes 和 64 位 chunk offsets。"""

    sample = b"\x08\x01"
    path = tmp_path / "compact.mp4"
    path.write_bytes(build_minimal_djmd_mp4(sample, compact_bits=compact_bits, use_co64=True))
    assert read_first_djmd_packet(path) == sample


def test_fixed_size_count_does_not_expand_in_memory(tmp_path: Path) -> None:
    """小文件里的大计数不应导致百万项列表分配。"""

    sample = b"\x08\x01"
    path = tmp_path / "count.mp4"
    path.write_bytes(build_minimal_djmd_mp4(sample, constant_count=1_000_000))
    tracemalloc.start()
    try:
        assert read_first_djmd_packet(path) == sample
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_non_djmd_sample_tables_are_not_read(tmp_path: Path) -> None:
    """无关视频轨的损坏 size 表不应阻止读取有效 djmd 轨。"""

    stsd = box("stsd", full_box_payload(struct.pack(">I", 1) + struct.pack(">I4s", 8, b"hvc1")))
    # 这些表故意不完整：目标轨识别之前读取它们会使本用例失败。
    stbl = box("stbl", stsd + box("stsz", b"x") + box("stsc", b"x") + box("stco", b"x"))
    other_trak = box("trak", box("mdia", box("minf", stbl)))
    path = tmp_path / "tracks.mp4"
    path.write_bytes(build_minimal_djmd_mp4(b"\x08\x01", extra_traks=other_trak))
    assert read_first_djmd_packet(path) == b"\x08\x01"


def test_deep_container_is_rejected_before_python_recursion(tmp_path: Path) -> None:
    """任意深的容器必须在业务边界内失败，而不是耗尽解释器递归。"""

    payload = box("free", b"")
    for _ in range(mp4_reader.MAX_BOX_DEPTH + 2):
        payload = box("moov", payload)
    path = tmp_path / "nested.mp4"
    path.write_bytes(payload)
    with pytest.raises(Mp4ReaderError, match="嵌套层级"):
        read_first_djmd_packet(path)


def test_sample_read_size_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首包超过限额时应在读取 payload 前拒绝。"""

    path = tmp_path / "large-packet.mp4"
    path.write_bytes(build_minimal_djmd_mp4(b"x" * 65))
    monkeypatch.setattr(mp4_reader, "MAX_METADATA_BYTES", 64)
    with pytest.raises(Mp4ReaderError, match="第一包超过允许大小"):
        read_first_djmd_packet(path)
