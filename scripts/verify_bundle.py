"""运行打包后的可执行程序自检，验证发布产物实际使用的代码和依赖。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """对指定可执行程序运行有超时的无窗口自检。"""

    executable = Path(sys.argv[1]).resolve()
    report_path = Path("build") / f"{executable.stem}-self-test.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)
    completed = subprocess.run(
        [str(executable), "--self-test", str(report_path.resolve())],
        check=False,
        timeout=120,
    )
    if not report_path.is_file():
        raise RuntimeError(f"发布包未生成自检报告，进程退出码：{completed.returncode}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if completed.returncode or not report["success"]:
        raise RuntimeError(report["error"] or f"发布包异常退出：{completed.returncode}")
    for message in report["checks"]:
        print(f"自检通过：{message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
