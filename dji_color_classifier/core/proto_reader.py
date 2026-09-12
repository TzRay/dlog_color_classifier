"""未知 schema 的 protobuf 轻量解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_PROTO_BYTES = 8 * 1024 * 1024
MAX_PROTO_FIELDS = 100_000
MAX_PROTO_DEPTH = 8


class ProtoReaderError(ValueError):
    """protobuf 数据损坏或超过读取边界。"""


@dataclass(frozen=True)
class ProtoField:
    """未知 schema 的 protobuf 字段。"""

    number: int
    wire_type: int
    value: int | bytes | list["ProtoField"] | None


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """读取 protobuf varint，并返回值和新的偏移。"""

    value = 0
    if offset < 0:
        raise ProtoReaderError("varint 偏移不能为负数")
    for byte_index in range(10):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        if byte_index == 9 and byte > 1:
            break
        value |= (byte & 0x7F) << (byte_index * 7)
        if byte < 0x80:
            return value, offset
    raise ProtoReaderError("无效或超出 64 位范围的 varint 数据")


def parse_proto(
    data: bytes, *, depth: int = 0, max_depth: int = MAX_PROTO_DEPTH, recursive: bool = True
) -> list[ProtoField]:
    """用未知 schema 方式解析 protobuf，尽量保留字段号和值。

    分类流程使用 ``recursive=False``，将 LEN 保留为 bytes，再按已知路径展开。
    默认递归模式保留诊断脚本接口；猜测的子消息无法解析时保留原始字符串或 bytes。
    当前层损坏始终抛出统一异常，避免把截断消息当作缺少字段。
    """

    if len(data) > MAX_PROTO_BYTES:
        raise ProtoReaderError("protobuf 数据超过允许大小")
    if not 0 <= depth <= max_depth <= MAX_PROTO_DEPTH:
        raise ProtoReaderError("protobuf 嵌套层级超过允许范围")
    fields: list[ProtoField] = []
    offset = 0
    while offset < len(data):
        if len(fields) >= MAX_PROTO_FIELDS:
            raise ProtoReaderError("protobuf 字段数量超过允许范围")
        key, offset = read_varint(data, offset)

        number = key >> 3
        wire_type = key & 0x07

        if not 0 < number < (1 << 29):
            raise ProtoReaderError("protobuf 字段编号无效")

        if wire_type == 0:
            value, offset = read_varint(data, offset)
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 1:
            if offset + 8 > len(data):
                raise ProtoReaderError("protobuf 64 位字段不完整")
            value = data[offset : offset + 8]
            offset += 8
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            if offset + length > len(data):
                raise ProtoReaderError("protobuf LEN 字段不完整")
            payload = data[offset : offset + length]
            offset += length
            if recursive and depth < max_depth and payload:
                try:
                    nested = parse_proto(payload, depth=depth + 1, max_depth=max_depth)
                except ProtoReaderError:
                    fields.append(ProtoField(number, wire_type, payload))
                else:
                    fields.append(ProtoField(number, wire_type, nested))
            else:
                fields.append(ProtoField(number, wire_type, payload))
        elif wire_type == 5:
            if offset + 4 > len(data):
                raise ProtoReaderError("protobuf 32 位字段不完整")
            value = data[offset : offset + 4]
            offset += 4
            fields.append(ProtoField(number, wire_type, value))
        else:
            raise ProtoReaderError(f"暂不支持的 protobuf wire type：{wire_type}")
    return fields


def nested_message(fields: list[ProtoField], number: int, index: int = 0) -> list[ProtoField]:
    """按字段号取得第 index 个嵌套 message。"""

    if index < 0:
        raise ProtoReaderError("protobuf 字段序号不能为负数")
    for field in fields:
        if field.number != number or field.wire_type != 2:
            continue
        if index:
            index -= 1
            continue
        if isinstance(field.value, bytes):
            return parse_proto(field.value, recursive=False)
        if isinstance(field.value, list):
            return field.value
    return []


def first_int(fields: list[ProtoField], number: int) -> int | None:
    """读取当前层第一个 varint 字段。"""

    for field in fields:
        if field.number == number and field.wire_type == 0 and isinstance(field.value, int):
            return field.value
    return None


def value_at_path(fields: list[ProtoField], path: Iterable[tuple[int, int]], value_field: int) -> int | None:
    """按嵌套路径读取 varint 值。"""

    current = fields
    for depth, (number, index) in enumerate(path, start=1):
        if depth > MAX_PROTO_DEPTH:
            raise ProtoReaderError("protobuf 字段路径超过允许层级")
        current = nested_message(current, number, index)
        if not current:
            return None
    return first_int(current, value_field)
