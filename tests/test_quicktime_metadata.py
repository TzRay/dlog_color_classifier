"""QuickTime ``mdta`` 色彩标签读取与分类测试。"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from dji_color_classifier.core.classifier import classify_file
from dji_color_classifier.core.models import ClassificationEvidence, ColorMode, ScanResult
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


def build_mdta_mp4(gamma_label: str, *, standard_meta: bool = False) -> bytes:
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
    return box("ftyp", b"isom\x00\x00\x02\x00isom") + box("moov", box("udta", meta_box))


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
