"""分类规则测试。"""

from __future__ import annotations

from dji_color_classifier.core.classifier import classify_djmd_packet
from dji_color_classifier.core.models import ColorMode


def field_varint(number: int, value: int) -> bytes:
    """构造 varint 字段。"""

    return encode_varint((number << 3) | 0) + encode_varint(value)


def field_message(number: int, payload: bytes) -> bytes:
    """构造 length-delimited message 字段。"""

    return encode_varint((number << 3) | 2) + encode_varint(len(payload)) + payload


def encode_varint(value: int) -> bytes:
    """编码 protobuf varint。"""

    data = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            data.append(byte | 0x80)
        else:
            data.append(byte)
            return bytes(data)


def color_gamma_packet(value: int) -> bytes:
    """构造包含 ColorGammaSxS 字段路径的 djmd payload。"""

    return field_message(2, field_message(2, field_message(3, field_varint(1, value))))


def record_mode_packet(value: int) -> bytes:
    """构造包含 record_mode 字段路径的 djmd payload。"""

    return field_message(2, field_message(3, field_varint(5, value)))


def test_classifies_dlog2() -> None:
    """ColorGammaSxS=22 应识别为 D-Log2。"""

    mode, evidence = classify_djmd_packet(color_gamma_packet(22))
    assert mode is ColorMode.DLOG2
    assert evidence.color_gamma_sxs == 22


def test_classifies_dlog() -> None:
    """ColorGammaSxS=2 应识别为 D-Log。"""

    mode, evidence = classify_djmd_packet(color_gamma_packet(2))
    assert mode is ColorMode.DLOG
    assert evidence.color_gamma_sxs == 2


def test_classifies_rec709() -> None:
    """ColorGammaSxS 缺失且 record_mode=8 应识别为普通 709。"""

    mode, evidence = classify_djmd_packet(record_mode_packet(8))
    assert mode is ColorMode.REC709
    assert evidence.record_mode == 8


def test_conflicting_explicit_label_and_djmd_enum_returns_unknown() -> None:
    """明确文本标签与已映射 djmd 枚举冲突时，不得静默选择其中之一。"""

    mode, evidence = classify_djmd_packet(color_gamma_packet(2), metadata_label="D-Log2")

    assert mode is ColorMode.UNKNOWN
    assert evidence.primary_source == "conflict"
    assert evidence.warnings
