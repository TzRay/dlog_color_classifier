"""GUI 结果表格的数据模型辅助类。"""

from __future__ import annotations

from dji_color_classifier.core.models import ExecutionRecord, PlanItem, ScanResult


class ResultTableModelMixin:
    """维护统一的结果行，避免扫描结果与整理计划按索引错配。"""

    headers = ["状态", "文件名", "色彩模式", "所在文件夹", "目标路径", "识别依据", "说明"]

    def set_results(self, results: list[ScanResult]) -> None:
        """显示扫描结果。"""

        self.beginResetModel()
        self.rows = list(results)
        self.plan = []
        self.execution_records = []
        self.endResetModel()

    def set_plan(self, plan: list[PlanItem]) -> None:
        """显示整理计划；每一行直接来自计划项自身的扫描结果。"""

        self.beginResetModel()
        self.plan = list(plan)
        self.rows = [item.scan_result for item in plan]
        self.execution_records = []
        self.endResetModel()

    def set_execution_records(self, records: list[ExecutionRecord]) -> None:
        """在当前计划上显示执行结果。"""

        self.beginResetModel()
        self.execution_records = list(records)
        self.endResetModel()

    def clear(self) -> None:
        """清空全部表格状态。"""

        self.beginResetModel()
        self.rows = []
        self.plan = []
        self.execution_records = []
        self.endResetModel()

    def rowCount(self, parent=None) -> int:  # noqa: N802
        """返回当前可见行数。"""

        return 0 if parent and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None) -> int:  # noqa: N802
        """返回列数。"""

        return 0 if parent and parent.isValid() else len(self.headers)
