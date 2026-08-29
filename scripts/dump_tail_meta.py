"""探测脚本：导出 MP4 尾部 DJI 键值元数据（mdta/com.dji.camera.*）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TAIL_SIZE = 8192


def printable(data: bytes) -> str:
    """把二进制转成可读文本，保留 ASCII 可打印字符。"""

    return "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else "." for b in data)


def extract_strings(data: bytes) -> list[str]:
    """提取连续 ASCII 可打印片段。"""

    strings: list[str] = []
    current: list[int] = []
    for b in data:
        if 32 <= b < 127:
            current.append(b)
        else:
            if len(current) >= 3:
                strings.append(bytes(current).decode("ascii"))
            current = []
    if len(current) >= 3:
        strings.append(bytes(current).decode("ascii"))
    return strings


def dump(path: Path) -> dict:
    """导出尾部 8KB 的可读文本和字符串片段。"""

    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - TAIL_SIZE))
        tail = handle.read()
    return {
        "file": path.name,
        "size": size,
        "tail_hex": tail.hex(),
        "tail_text": printable(tail),
        "strings": extract_strings(tail),
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：对每个文件输出 JSON。"""

    parser = argparse.ArgumentParser(description="导出 MP4 尾部 DJI 元数据")
    parser.add_argument("paths", nargs="+", type=Path, help="MP4/MOV 文件")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    results = [dump(path) for path in args.paths]
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
