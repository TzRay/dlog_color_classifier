"""QuickTime ``mdta`` 色彩标签读取与分类测试。"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from dji_color_classifier.core.classifier import classify_file
from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
from dji_color_classifier.core import mp4_reader
from dji_color_classifier.core.mp4_reader import DJI_COLOR_GAMMA_KEY, read_quicktime_metadata
from dji_color_classifier.core.report import write_report


def box(box_type: str | bytes, payload: bytes) -> bytes:
    """构造普通 32 位 size 的 MP4 box，允许 ``ilst`` 使用数值类型。"""

    raw_type = box_type.encode("latin1") if isinstance(box_type, str) else box_type
    return struct.pack(">I4s", len(payload) + 8, raw_type) + payload


def key_entry(key: str) -> bytes:
    """构造 ``keys`` 中的一个 mdta 命名空间条目。"""

    raw_key = key.encode("utf-8")
    return struct.pack(">I4s", len(raw_key) + 8, b"mdta") + raw_key


def data_box(value: str) -> bytes:
    """构造 QuickTime 文本 ``data`` box。"""

    # 前 4 字节为 data type/flags，接着 4 字节为 locale，二者均不属于文本内容。
    return box("data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + value.encode("utf-8"))


def build_mdta_mp4(gamma_label: str, *, standard_meta: bool = False, sample: bytes | None = None) -> bytes:
    """构造不含视频轨、只含 DJI mdta 标签的最小 MP4。"""

    keys = ["com.example.Note", DJI_COLOR_GAMMA_KEY]
    keys_box = box("keys", b"\x00\x00\x00\x00" + struct.pack(">I", len(keys)) + b"".join(map(key_entry, keys)))
    # 故意让 ilst 条目顺序与 keys 相反，验证实现按索引而不是出现顺序关联。
    gamma_item = box(struct.pack(">I", 2), data_box(gamma_label))
    note_item = box(struct.pack(">I", 1), data_box("not-a-color-mode"))
    ilst_box = box("ilst", gamma_item + note_item)
    hdlr_box = box("hdlr", b"\x00" * 24)
    meta_prefix = b"\x00\x00\x00\x00" if standard_meta else b""
    meta_box = box("meta", meta_prefix + hdlr_box + keys_box + ilst_box)
    ftyp = box("ftyp", b"isom\x00\x00\x02\x00isom")
    mdat = b""
    trak = b""
    if sample is not None:
        mdat = box("mdat", sample)
        prefix = b"\x00" * 4
        stsd = box("stsd", prefix + struct.pack(">I", 1) + struct.pack(">I4s", 8, b"djmd"))
        stsz = box("stsz", prefix + struct.pack(">III", 0, 1, len(sample)))
        stsc = box("stsc", prefix + struct.pack(">IIII", 1, 1, 1, 1))
        stco = box("stco", prefix + struct.pack(">II", 1, len(ftyp) + 8))
        trak = box("trak", box("mdia", box("minf", box("stbl", stsd + stsz + stsc + stco))))
    return ftyp + mdat + box("moov", box("udta", meta_box) + trak)


def test_reads_dji_gamma_label_by_keys_index(tmp_path: Path) -> None:
    """读取器应按 keys 索引找到 DJI 色彩标签，而非依赖字段顺序。"""

    path = tmp_path / "metadata.mp4"
    path.write_bytes(build_mdta_mp4("Rec.709"))

    metadata = read_quicktime_metadata(path)

    assert metadata[DJI_COLOR_GAMMA_KEY] == "Rec.709"
    assert metadata["com.example.Note"] == "not-a-color-mode"


def test_reads_standard_fullbox_meta(tmp_path: Path) -> None:
    """ISO BMFF FullBox 形式的 meta 也应正确跳过 version/flags。"""

    path = tmp_path / "standard-meta.mp4"
    path.write_bytes(build_mdta_mp4("D-Log", standard_meta=True))

    assert read_quicktime_metadata(path)[DJI_COLOR_GAMMA_KEY] == "D-Log"


def test_classifies_hlg_from_explicit_metadata_without_djmd(tmp_path: Path) -> None:
    """没有 djmd 轨时，明确的 Rec.2100 HLG 标签仍应完成分类。"""

    path = tmp_path / "hlg.mp4"
    path.write_bytes(build_mdta_mp4("Rec.2100 HLG"))

    result = classify_file(path)

    assert result.mode is ColorMode.REC2100_HLG
    assert result.evidence.primary_source == "quicktime_mdta"
    assert result.evidence.confidence == "high"
    assert result.error is None


def test_report_preserves_metadata_evidence_fields(tmp_path: Path) -> None:
    """JSON 报告应输出新增的标签、主证据、置信度和冲突字段。"""

    source = tmp_path / "hlg.mp4"
    result = ScanResult(
        source,
        ColorMode.REC2100_HLG,
        ClassificationEvidence(
            None,
            10,
            metadata_label="Rec.2100 HLG",
            primary_source="quicktime_mdta",
            confidence="high",
        ),
    )
    output = tmp_path / "report.json"

    write_report([result], output, fmt="json")

    row = json.loads(output.read_text(encoding="utf-8"))[0]
    assert row["QuickTime色彩标签"] == "Rec.2100 HLG"
    assert row["主证据来源"] == "quicktime_mdta"
    assert row["置信度"] == "high"


def test_invalid_container_is_reported_as_error(tmp_path: Path) -> None:
    """损坏且没有任何可用证据的文件应保留为识别失败。"""

    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not an mp4")

    result = classify_file(path)

    assert result.mode is ColorMode.ERROR
    assert result.error is not None


def test_damaged_djmd_preserves_independent_mdta_label(tmp_path: Path) -> None:
    """容器可读而 djmd 截断时，明确的 mdta 标签应正常分类并显示警告。"""

    path = tmp_path / "recoverable.mp4"
    path.write_bytes(build_mdta_mp4("D-Log2", sample=b"\x08\x80"))
    result = classify_file(path)
    assert result.mode is ColorMode.DLOG2
    assert result.error is None
    assert result.evidence.warnings
    assert result.evidence.primary_source == "quicktime_mdta"


def test_damaged_djmd_without_known_label_is_single_file_error(tmp_path: Path) -> None:
    """无可用独立证据时，损坏的 djmd 应返回单文件失败结果。"""

    path = tmp_path / "damaged-packet.mp4"
    path.write_bytes(build_mdta_mp4("", sample=b"\x08\x80"))
    result = classify_file(path)
    assert result.mode is ColorMode.ERROR
    assert result.error is not None
    assert "protobuf" in result.error


def test_bad_sample_table_does_not_discard_mdta(tmp_path: Path) -> None:
    """djmd sample 表读取失败不能丢弃独立的明确标签。"""

    payload = bytearray(build_mdta_mp4("D-Log2", sample=b"\x08\x01"))
    stsz_start = payload.index(b"stsz")
    struct.pack_into(">I", payload, stsz_start + 12, 0)
    path = tmp_path / "bad-table.mp4"
    path.write_bytes(payload)
    result = classify_file(path)
    assert result.mode is ColorMode.DLOG2
    assert result.evidence.warnings
    assert result.error is None


def test_bad_quicktime_keys_does_not_discard_djmd(tmp_path: Path) -> None:
    """mdta 键表损坏时，可独立使用已知 djmd 枚举。"""

    sample = b"\x12\x06\x12\x04\x1a\x02\x08\x16"
    payload = bytearray(build_mdta_mp4("D-Log2", sample=sample))
    keys_start = payload.index(b"keys")
    struct.pack_into(">I", payload, keys_start + 8, 1000)
    path = tmp_path / "bad-keys.mp4"
    path.write_bytes(payload)
    result = classify_file(path)
    assert result.mode is ColorMode.DLOG2
    assert result.evidence.primary_source == "djmd_gamma_enum"
    assert result.evidence.warnings
    assert result.error is None


def test_classification_opens_and_parses_container_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两种色彩来源共用文件句柄和顶层 box 树，避免重复磁盘扫描。"""

    path = tmp_path / "single-read.mp4"
    path.write_bytes(build_mdta_mp4("D-Log2", sample=b"\x12\x06\x12\x04\x1a\x02\x08\x16"))
    original_open = Path.open
    original_parse = mp4_reader._parse_children
    opened_paths: list[Path] = []
    top_level_parses: list[int] = []

    def record_open(opened_path: Path, *args: object, **kwargs: object):
        """记录真实文件打开，保留文件对象及 fstat 行为。"""

        opened_paths.append(opened_path)
        return original_open(opened_path, *args, **kwargs)

    def record_parse(handle, start: int, end: int, **kwargs):
        """仅统计起点为零的完整容器解析，排除 ilst 内部子项读取。"""

        if start == 0:
            top_level_parses.append(start)
        return original_parse(handle, start, end, **kwargs)

    monkeypatch.setattr(Path, "open", record_open)
    monkeypatch.setattr(mp4_reader, "_parse_children", record_parse)
    result = classify_file(path)
    assert result.mode is ColorMode.DLOG2
    assert opened_paths == [path]
    assert len(top_level_parses) == 1


def test_quicktime_payload_read_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超大文本元数据应产生明确错误，不能无上限读取。"""

    path = tmp_path / "large-mdta.mp4"
    path.write_bytes(build_mdta_mp4("D-Log" + " " * 200))
    monkeypatch.setattr(mp4_reader, "MAX_METADATA_BYTES", 128)
    with pytest.raises(mp4_reader.Mp4ReaderError, match="超过允许大小"):
        read_quicktime_metadata(path)
