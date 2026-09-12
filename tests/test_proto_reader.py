"""protobuf 惰性读取与损坏输入边界回归测试。"""

from __future__ import annotations

import pytest

from dji_color_classifier.core import proto_reader
from dji_color_classifier.core.proto_reader import ProtoReaderError, parse_proto, read_varint, value_at_path


def test_lazy_reader_preserves_unrelated_string() -> None:
    """LEN 字段里的字符串不能被强制解释为嵌套消息。"""

    fields = parse_proto(b"\x0a\x01x", recursive=False)
    assert fields[0].value == b"x"
    assert parse_proto(b"\x0a\x01x")[0].value == b"x"


@pytest.mark.parametrize("packet", [b"\x08\x80", b"\x12\x80", b"\x12\x04x", b"\x09x", b"\x0dx", b"\x00"])
def test_truncated_fields_raise_consistent_error(packet: bytes) -> None:
    """损坏字段不能被静默视为缺少枚举。"""

    with pytest.raises(ProtoReaderError):
        parse_proto(packet, recursive=False)


def test_varint_accepts_uint64_and_rejects_overflow() -> None:
    """限制 varint 为 64 位，同时允许完整的 uint64 最大值。"""

    assert read_varint(b"\xff" * 9 + b"\x01", 0) == ((1 << 64) - 1, 10)
    with pytest.raises(ProtoReaderError):
        read_varint(b"\xff" * 9 + b"\x02", 0)


def test_known_path_rejects_malformed_child() -> None:
    """只有明确要求展开的消息损坏时才影响分类路径。"""

    fields = parse_proto(b"\x12\x01x", recursive=False)
    with pytest.raises(ProtoReaderError):
        value_at_path(fields, [(2, 0)], 1)


def test_path_depth_is_bounded() -> None:
    """过深字段路径应产生可诊断错误，而非继续递归。"""

    packet = b"\x08\x01"
    for _ in range(proto_reader.MAX_PROTO_DEPTH + 1):
        packet = b"\x12" + bytes([len(packet)]) + packet
    fields = parse_proto(packet, recursive=False)
    with pytest.raises(ProtoReaderError, match="层级"):
        value_at_path(fields, [(2, 0)] * (proto_reader.MAX_PROTO_DEPTH + 1), 1)


def test_message_size_and_field_count_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """输入大小和字段数量各自限制，避免构造无上限的 Python 对象。"""

    monkeypatch.setattr(proto_reader, "MAX_PROTO_FIELDS", 2)
    with pytest.raises(ProtoReaderError, match="字段数量"):
        parse_proto(b"\x08\x01" * 3, recursive=False)
    monkeypatch.setattr(proto_reader, "MAX_PROTO_BYTES", 3)
    with pytest.raises(ProtoReaderError, match="大小"):
        parse_proto(b"\x08\x01" * 2, recursive=False)
