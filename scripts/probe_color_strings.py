"""探测脚本：在 MP4 元数据区域搜索色彩模式相关字符串。

DJI 机型有时会在 moov/udta 或 XMP 中写入 ColorMode / HLG / D-Log 等字样，
本脚本扫描文件头尾各 16MB，输出命中的可读字符串，用于交叉验证 djmd 判定。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

PATTERN = re.compile(rb"(hlg|dlog|log ?m|color ?mode|gamma|rec\.?709|bt\.?709|bt\.?2020)", re.IGNORECASE)
HEAD_TAIL = 16 * 1024 * 1024


def readable_context(data: bytes, start: int, end: int) -> str:
    """截取命中位置附近的可打印 ASCII 文本。"""

    lo = max(0, start - 80)
    hi = min(len(data), end + 80)
    chunk = data[lo:hi]
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    return text


def probe(path: Path) -> dict:
    """扫描单个文件并返回命中的字符串及上下文。"""

    size = path.stat().st_size
    hits: list[dict] = []
    with path.open("rb") as handle:
        head = handle.read(HEAD_TAIL)
        tail_start = max(0, size - HEAD_TAIL)
        if tail_start >= HEAD_TAIL:
            handle.seek(tail_start)
            tail = handle.read()
        else:
            tail = b""

    for data, offset_base in ((head, 0), (tail, tail_start)):
        for match in PATTERN.finditer(data):
            hits.append(
                {
                    "offset": offset_base + match.start(),
                    "keyword": match.group(0).decode("ascii", "replace"),
                    "context": readable_context(data, match.start(), match.end()),
                }
            )
    return {"file": path.name, "size": size, "hits": hits[:40]}


def main(argv: list[str] | None = None) -> int:
    """命令行入口：对每个文件输出 JSON。"""

    parser = argparse.ArgumentParser(description="探测 MP4 中的色彩模式字符串")
    parser.add_argument("paths", nargs="+", type=Path, help="MP4/MOV 文件")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    results = [probe(path) for path in args.paths]
    text = __import__("json").dumps(results, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
