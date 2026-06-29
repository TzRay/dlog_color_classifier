"""GUI 结果表格模型。"""

from __future__ import annotations

from dji_color_classifier.core.models import PlanItem, ScanResult


class ResultTableModelMixin:
    """表格数据处理 mixin，实际 Qt 类在 main_window 中动态组合。"""

    headers = ["状态", "文件名", "色彩模式", "原路径", "目标路径", "证据", "错误"]

    def set_rows(self, results: list[ScanResult], plan: list[PlanItem] | None = None) -> None:
        """刷新表格数据。"""

        self.beginResetModel()
        self.results = results
        self.plan = plan or []
        self.endResetModel()

    def rowCount(self, parent=None) -> int:  # noqa: N802
        """返回行数。"""

        return 0 if parent and parent.isValid() else len(self.results)

    def columnCount(self, parent=None) -> int:  # noqa: N802
        """返回列数。"""

        return 0 if parent and parent.isValid() else len(self.headers)
