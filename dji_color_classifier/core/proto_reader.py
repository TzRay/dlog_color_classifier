"""未知 schema 的 protobuf 轻量解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProtoField:
    """未知 schema 的 protobuf 字段。"""

    number: int
    wire_type: int
    value: int | bytes | list["ProtoField"] | None


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """读取 protobuf varint，并返回值和新的偏移。"""

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
    """用未知 schema 方式解析 protobuf，尽量保留字段号和值。

    DJI 的 `djmd` 数据是嵌套 protobuf。这里不依赖 `.proto` 文件，只递归解析
    length-delimited 字段，便于从稳定字段路径中读取枚举值。
    """

    fields: list[ProtoField] = []
    offset = 0
    while offset < len(data):
        try:
            key, offset = read_varint(data, offset)
        except ValueError:
            break

        number = key >> 3
        wire_type = key & 0x07

        if number <= 0:
            break

        if wire_type == 0:
            value, offset = read_varint(data, offset)
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 1:
            if offset + 8 > len(data):
                break
            value = data[offset : offset + 8]
            offset += 8
            fields.append(ProtoField(number, wire_type, value))
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            if offset + length > len(data):
                break
            payload = data[offset : offset + length]
            offset += length
            if depth < max_depth and payload:
                nested = parse_proto(payload, depth=depth + 1, max_depth=max_depth)
                fields.append(ProtoField(number, wire_type, nested))
            else:
                fields.append(ProtoField(number, wire_type, payload))
        elif wire_type == 5:
            if offset + 4 > len(data):
                break
            value = data[offset : offset + 4]
            offset += 4
            fields.append(ProtoField(number, wire_type, value))
        else:
            # group 等老格式不是当前 DJI 样本需要的格式，遇到后保守停止。
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
