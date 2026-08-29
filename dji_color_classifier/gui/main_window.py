"""轻量单页式 DJI 色彩模式识别与整理主窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dji_color_classifier.core.executor import build_undo_plan, execute_plan
from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import ConflictPolicy, ExecutionRecord, PlanItem, ScanResult
from dji_color_classifier.core.planner import build_plan
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import scan_directory, summarize_results
from dji_color_classifier.gui.table_model import ResultTableModelMixin
from dji_color_classifier.gui.qt_compat import (
    QApplication,
    QAbstractTableModel,
    QButtonGroup,
    QCheckBox,
    QColor,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QModelIndex,
    QObject,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRunnable,
    QTableView,
    QThreadPool,
    QVBoxLayout,
    QWidget,
    Signal,
    Slot,
    display_role,
    foreground_role,
    horizontal_orientation,
    interactive_resize,
    tooltip_role,
    yes_button,
)


@dataclass(frozen=True)
class ExecutionOutcome:
    """后台执行结果，包含 manifest 写入状态。"""

    token: int
    records: list[ExecutionRecord]
    manifest_path: Path | None
    manifest_error: str | None = None


class TaskSignals(QObject):
    """后台任务统一信号。"""

    finished = Signal(object)
    failed = Signal(object)


class ScanWorker(QRunnable):
    """后台扫描任务。"""

    def __init__(self, token: int, directory: Path, recursive: bool) -> None:
        super().__init__()
        self.token = token
        self.directory = directory
        self.recursive = recursive
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        """执行扫描，并将任务令牌随结果返回。"""

        try:
            results = scan_directory(self.directory, recursive=self.recursive)
            self.signals.finished.emit((self.token, self.directory, results))
        except Exception as exc:
            self.signals.failed.emit(("scan", self.token, str(exc)))


class ExecutionWorker(QRunnable):
    """后台执行整理或撤销计划，避免复制大文件时阻塞窗口。"""

    def __init__(self, token: int, root: Path, operation: str, plan: list[PlanItem]) -> None:
        super().__init__()
        self.token = token
        self.root = root
        self.operation = operation
        self.plan = list(plan)
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        """执行计划，并尽最大可能保存可撤销记录。"""

        try:
            records = execute_plan(self.plan, apply=True)
        except Exception as exc:
            self.signals.failed.emit(("execution", self.token, str(exc)))
            return

        manifest_path: Path | None = None
        manifest_error: str | None = None
        try:
            manifest = create_manifest(self.root, self.operation, records)
            manifest_path = write_manifest(manifest)
        except Exception as exc:
            # 文件操作可能已经完成，因此必须将执行记录交回界面，不能只报“任务失败”。
            manifest_error = str(exc)
        self.signals.finished.emit(ExecutionOutcome(self.token, records, manifest_path, manifest_error))


class ResultTableModel(ResultTableModelMixin, QAbstractTableModel):
    """统一显示扫描结果、整理计划和执行结果。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[ScanResult] = []
        self.plan: list[PlanItem] = []
        self.execution_records: list[ExecutionRecord] = []

    def data(self, index: QModelIndex, role: int = 0):  # noqa: ANN001,N802
        """返回单元格文本或状态颜色。"""

        if not index.isValid() or index.row() >= len(self.rows):
            return None
        result = self.rows[index.row()]
        item = self.plan[index.row()] if index.row() < len(self.plan) else None
        record = self.execution_records[index.row()] if index.row() < len(self.execution_records) else None

        if role == foreground_role():
            if item and item.skipped:
                return QColor("#b46a00")
            if record:
                return QColor("#278a49" if record.success else "#b42318")
            if result.error or result.mode.value in {"unknown", "error"}:
                return QColor("#b42318")
            return None
        if role == tooltip_role() and index.column() in {1, 3}:
            return str(result.path)
        if role != display_role():
            return None

        needs_attention = bool(result.error) or result.mode.value in {"unknown", "error"}
        status = "需要检查" if needs_attention else "已识别"
        note = result.error or ("无法从元数据确认色彩模式" if needs_attention else "")
        if item:
            status = "跳过" if item.skipped else "待执行"
            note = item.reason or note
        if record and item and item.skipped:
            status = "跳过"
            note = item.reason or record.message
        elif record:
            status = "成功" if record.success else "失败"
            note = record.message
        values = [
            status,
            result.path.name,
            result.mode.label,
            result.path.parent.name or str(result.path.parent),
            str(item.target) if item and item.target else "",
            result.evidence.detail,
            note,
        ]
        return values[index.column()]

    def headerData(self, section: int, orientation, role: int = 0):  # noqa: ANN001,N802
        """返回中文表头。"""

        if role == display_role() and orientation == horizontal_orientation():
            return self.headers[section]
        return None


class MainWindow(QMainWindow):
    """面向普通用户的轻量单页主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DJI 视频色彩模式识别与整理")
        self.resize(1120, 780)
        self.setMinimumSize(900, 660)

        self.thread_pool = QThreadPool.globalInstance()
        self.results: list[ScanResult] = []
        self.plan: list[PlanItem] = []
        self._scan_token = 0
        self._execution_token = 0
        self._active_workers: dict[tuple[str, int], QRunnable] = {}
        self._busy = False
        self._busy_kind: str | None = None
        self._undo_mode = False
        self._scan_root: Path | None = None
        self._scan_recursive = True
        self._operation_root: Path | None = None
        self._execution_completed = False
        self._build_ui()
        self._apply_style()
        self._reset_interface()

    def _build_ui(self) -> None:
        """构建居中的单页工作区。"""

        root = QWidget()
        root.setObjectName("appBackground")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(24, 10, 24, 12)
        root_layout.setSpacing(0)
        root_layout.addStretch(1)

        content = QFrame()
        content.setObjectName("content")
        content.setMaximumWidth(1080)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 14, 20, 14)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self._build_results_page(), 1)
        root_layout.addWidget(content, 10)
        root_layout.addStretch(1)
        self.setCentralWidget(root)
        self.setAcceptDrops(True)

    def _build_header(self) -> QWidget:
        """构建简洁标题和低频操作入口。"""

        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        brand = QLabel("D")
        brand.setObjectName("brandMark")
        brand.setAlignment(self._align_center())
        brand.setFixedSize(44, 44)
        layout.addWidget(brand)

        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("DJI 视频色彩识别")
        title.setObjectName("appTitle")
        title_column.addWidget(title)
        subtitle = QLabel("选择文件夹，快速识别 D-Log、D-Log2、Rec.709 与 HLG HDR")
        subtitle.setObjectName("mutedText")
        title_column.addWidget(subtitle)
        layout.addLayout(title_column)
        layout.addStretch(1)

        self.advanced_button = QPushButton("设置")
        self.advanced_button.setObjectName("quietButton")
        self.advanced_button.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_button)
        self.undo_button = QPushButton("操作记录")
        self.undo_button.setObjectName("quietButton")
        self.undo_button.clicked.connect(self.undo_from_manifest)
        layout.addWidget(self.undo_button)
        return header

    def _build_results_page(self) -> QWidget:
        """构建选择、识别和整理均在同一屏完成的主页面。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        folder_card = QFrame()
        folder_card.setObjectName("card")
        folder_card.setMaximumHeight(210)
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(18, 14, 18, 14)
        folder_layout.setSpacing(8)

        folder_header = QHBoxLayout()
        folder_title_column = QVBoxLayout()
        folder_title_column.setSpacing(2)
        folder_title = QLabel("选择 DJI 视频文件夹")
        folder_title.setObjectName("sectionTitle")
        folder_title_column.addWidget(folder_title)
        self.folder_label = QLabel("拖入文件夹，或点击右侧按钮；选择后会立即识别")
        self.folder_label.setObjectName("mutedText")
        self.folder_label.setWordWrap(True)
        folder_title_column.addWidget(self.folder_label)
        folder_header.addLayout(folder_title_column, 1)
        self.choose_button = QPushButton("选择文件夹")
        self.choose_button.setObjectName("selectButton")
        self.choose_button.clicked.connect(self.choose_directory)
        folder_header.addWidget(self.choose_button)
        folder_layout.addLayout(folder_header)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setAccessibleName("DJI 视频文件夹路径")
        self.path_edit.setPlaceholderText("也可以粘贴文件夹路径后按 Enter")
        self.path_edit.textChanged.connect(self._on_path_changed)
        self.path_edit.returnPressed.connect(self.start_scan)
        path_row.addWidget(self.path_edit, 1)
        self.rescan_button = QPushButton("重新识别")
        self.rescan_button.setObjectName("quietButton")
        self.rescan_button.clicked.connect(self.start_scan)
        path_row.addWidget(self.rescan_button)
        folder_layout.addLayout(path_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        folder_layout.addWidget(self.progress)
        self.scan_state_label = QLabel("等待选择文件夹")
        self.scan_state_label.setObjectName("statusText")
        folder_layout.addWidget(self.scan_state_label)
        self.empty_label = QLabel("识别速度很快，无需预先配置任何选项")
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(self._align_center())
        self.empty_label.setFixedHeight(52)
        folder_layout.addWidget(self.empty_label)
        layout.addWidget(folder_card)

        self.results_panel = QFrame()
        self.results_panel.setObjectName("resultsPanel")
        results_layout = QVBoxLayout(self.results_panel)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(10)

        result_header = QHBoxLayout()
        result_title_column = QVBoxLayout()
        result_title_column.setSpacing(2)
        result_title = QLabel("识别完成")
        result_title.setObjectName("resultTitle")
        result_title_column.addWidget(result_title)
        self.selection_label = QLabel("尚无识别结果")
        self.selection_label.setObjectName("mutedText")
        result_title_column.addWidget(self.selection_label)
        result_header.addLayout(result_title_column)
        result_header.addStretch(1)
        self.export_button = QPushButton("导出报告")
        self.export_button.setObjectName("quietButton")
        self.export_button.clicked.connect(self.export_report)
        result_header.addWidget(self.export_button)
        self.details_button = QPushButton("查看文件明细")
        self.details_button.setObjectName("quietButton")
        self.details_button.clicked.connect(self._toggle_details)
        result_header.addWidget(self.details_button)
        results_layout.addLayout(result_header)
        results_layout.addWidget(self._build_summary())

        self.attention_label = QLabel()
        self.attention_label.setObjectName("warningBanner")
        self.attention_label.setWordWrap(True)
        self.attention_label.setVisible(False)
        results_layout.addWidget(self.attention_label)
        self.organize_card = self._build_organize_card()
        self.organize_card.setMaximumHeight(240)
        results_layout.addWidget(self.organize_card)

        self.details_panel = QFrame()
        self.details_panel.setObjectName("card")
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(16, 14, 16, 16)
        details_layout.setSpacing(10)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("识别状态筛选")
        self.status_filter.addItem("全部状态", "all")
        self.status_filter.addItem("已识别", "ready")
        self.status_filter.addItem("需要检查", "attention")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.status_filter)
        self.mode_filter = QComboBox()
        self.mode_filter.setAccessibleName("色彩模式筛选")
        self.mode_filter.addItem("全部色彩模式", "all")
        for text, value in (
            ("D-Log", "dlog"),
            ("D-Log2", "dlog2"),
            ("普通709", "rec709"),
            ("Rec.2100 HLG（HDR）", "rec2100_hlg"),
            ("无法确认", "unknown"),
        ):
            self.mode_filter.addItem(text, value)
        self.mode_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.mode_filter)
        self.search_edit = QLineEdit()
        self.search_edit.setAccessibleName("搜索视频文件")
        self.search_edit.setPlaceholderText("搜索文件名或路径")
        self.search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_edit, 1)
        details_layout.addLayout(filter_row)

        self.model = ResultTableModel()
        self.table = QTableView()
        self.table.setAccessibleName("视频识别结果")
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
            if hasattr(QTableView, "SelectionBehavior")
            else QTableView.SelectRows
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(interactive_resize())
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 240)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column in (4, 5, 6):
            self.table.setColumnHidden(column, True)
        self.table.setMinimumHeight(220)
        details_layout.addWidget(self.table, 1)
        self.details_panel.setVisible(False)
        results_layout.addWidget(self.details_panel, 1)
        self.results_panel.setVisible(False)
        layout.addWidget(self.results_panel, 1)

        self.advanced_group = self._build_advanced_group()
        self.advanced_group.setVisible(False)
        layout.addWidget(self.advanced_group)
        self.log = QPlainTextEdit()
        self.log.setAccessibleName("运行日志")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setVisible(False)
        layout.addWidget(self.log)
        return page

    def _build_summary(self) -> QWidget:
        """构建紧凑、直观的识别数量概览。"""

        summary = QFrame()
        summary.setObjectName("summary")
        summary.setMaximumHeight(120)
        layout = QGridLayout(summary)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setHorizontalSpacing(8)
        self.metric_labels: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            (
                ("全部", "全部视频"),
                ("D-Log", "D-Log"),
                ("D-Log2", "D-Log2"),
                ("普通709", "普通 709"),
                ("Rec.2100 HLG（HDR）", "HLG HDR"),
                ("无法确认", "待确认"),
            ),
        ):
            title_label = QLabel(title)
            title_label.setObjectName("metricTitle")
            title_label.setAlignment(self._align_center())
            value_label = QLabel("0")
            value_label.setObjectName("metricValue")
            value_label.setAlignment(self._align_center())
            self.metric_labels[key] = value_label
            layout.addWidget(title_label, 0, column)
            layout.addWidget(value_label, 1, column)
            layout.setColumnStretch(column, 1)
        return summary

    def _build_advanced_group(self) -> QGroupBox:
        """构建不干扰主流程的高级设置。"""

        group = QGroupBox("高级设置")
        group.setObjectName("advancedGroup")
        layout = QGridLayout(group)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)
        self.recursive_check = QCheckBox("识别时包含子文件夹")
        self.recursive_check.setChecked(True)
        self.recursive_check.stateChanged.connect(self._on_recursive_changed)
        self.conflict_combo = QComboBox()
        self.conflict_combo.setAccessibleName("同名文件处理方式")
        self.conflict_combo.addItem("发现同名文件时停止", "error")
        self.conflict_combo.addItem("跳过同名文件", "skip")
        self.conflict_combo.addItem("自动追加序号", "suffix")
        self.name_template_edit = QLineEdit()
        self.name_template_edit.setAccessibleName("文件名模板")
        self.name_template_edit.setPlaceholderText("例如：{mode}_{original}")
        self.dir_template_edit = QLineEdit()
        self.dir_template_edit.setAccessibleName("目录模板")
        self.dir_template_edit.setPlaceholderText("例如：{mode}")
        self.sidecar_check = QCheckBox("同时整理同名字幕、缩略图等伴随文件")
        self.log_button = QPushButton("查看运行日志")
        self.log_button.setObjectName("quietButton")
        self.log_button.clicked.connect(self._toggle_log)
        layout.addWidget(self.recursive_check, 0, 0, 1, 2)
        layout.addWidget(QLabel("同名文件"), 1, 0)
        layout.addWidget(self.conflict_combo, 1, 1)
        layout.addWidget(QLabel("文件名模板"), 2, 0)
        layout.addWidget(self.name_template_edit, 2, 1)
        layout.addWidget(QLabel("分类目录模板"), 3, 0)
        layout.addWidget(self.dir_template_edit, 3, 1)
        layout.addWidget(self.sidecar_check, 4, 0, 1, 2)
        layout.addWidget(self.log_button, 5, 0, 1, 2)
        for control in (self.conflict_combo, self.name_template_edit, self.dir_template_edit, self.sidecar_check):
            signal = (
                control.currentIndexChanged
                if isinstance(control, QComboBox)
                else (control.textChanged if isinstance(control, QLineEdit) else control.stateChanged)
            )
            signal.connect(self._invalidate_plan)
        return group

    def _build_organize_card(self) -> QWidget:
        """构建无需页面跳转的整理方式与主操作卡片。"""

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        title = QLabel("如何整理这些视频？")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("默认使用复制方式，原始文件不会被修改。")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)

        # 保留 QComboBox 作为稳定的数据接口，实际界面使用更直观的三张选择卡。
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("添加文件名前缀", "prefix")
        self.mode_combo.addItem("移动到分类文件夹", "move")
        self.mode_combo.addItem("复制到分类文件夹（推荐）", "copy")
        self.mode_combo.setCurrentIndex(2)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setVisible(False)

        options = QHBoxLayout()
        options.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        option_specs = (
            ("copy", "复制到分类文件夹\n推荐 · 保留原始文件"),
            ("move", "移动到分类文件夹\n节省空间 · 改变文件位置"),
            ("prefix", "添加文件名前缀\n不创建新的分类文件夹"),
        )
        self.mode_option_texts = dict(option_specs)
        for mode, text in option_specs:
            button = QPushButton(text)
            button.setObjectName("modeOption")
            button.setCheckable(True)
            button.setMinimumHeight(60)
            button.clicked.connect(lambda _checked=False, value=mode: self._select_mode(value))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            options.addWidget(button, 1)
        self.mode_buttons["copy"].setChecked(True)
        layout.addLayout(options)

        self.action_hint = QLabel()
        self.action_hint.setObjectName("actionBanner")
        self.action_hint.setWordWrap(True)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.action_hint, 1)
        self.apply_button = QPushButton("复制并整理")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self.apply_current_plan)
        self.apply_button.setEnabled(False)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        self.action_status_label = QLabel()
        self.action_status_label.setObjectName("mutedText")
        self.action_status_label.setVisible(False)
        layout.addWidget(self.action_status_label)
        return card

    @staticmethod
    def _align_center():  # noqa: ANN205
        """兼容 Qt5/Qt6 返回居中对齐枚举。"""

        from dji_color_classifier.gui.qt_compat import Qt

        return getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter")

    def _apply_style(self) -> None:
        """应用克制、清晰的浅色桌面工具主题。"""

        self.setStyleSheet(
            """
            QWidget {
                color: #1f2937;
                font-family: "Microsoft YaHei UI";
                font-size: 14px;
            }
            QMainWindow, QWidget#appBackground { background: #f4f5f7; }
            QFrame#content { background: transparent; }
            QLabel#brandMark {
                color: #ffffff;
                background: #d92d20;
                border-radius: 10px;
                font-size: 22px;
                font-weight: 800;
            }
            QLabel#appTitle { font-size: 23px; font-weight: 700; }
            QLabel#sectionTitle { font-size: 17px; font-weight: 700; }
            QLabel#resultTitle { color: #067647; font-size: 20px; font-weight: 700; }
            QLabel#mutedText { color: #667085; font-size: 13px; }
            QLabel#statusText { color: #475467; font-size: 13px; }
            QLabel#emptyState {
                color: #667085;
                background: #f9fafb;
                border: 1px dashed #d0d5dd;
                border-radius: 8px;
                padding: 12px;
            }
            QFrame#card, QFrame#summary {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 10px;
            }
            QFrame#resultsPanel { background: transparent; border: 0; }
            QFrame#summary { border-radius: 9px; }
            QLabel#metricTitle { color: #667085; font-size: 12px; font-weight: 600; }
            QLabel#metricValue { font-size: 25px; font-weight: 700; }
            QLabel#warningBanner {
                color: #934009;
                background: #fff7ed;
                border: 1px solid #fed7aa;
                border-radius: 8px;
                padding: 10px 12px;
            }
            QLabel#actionBanner {
                color: #344054;
                background: #f9fafb;
                border: 1px solid #eaecf0;
                border-radius: 8px;
                padding: 11px 13px;
                font-size: 14px;
            }
            QPushButton, QComboBox, QLineEdit {
                min-height: 40px;
                background: #ffffff;
                border: 1px solid #d0d5dd;
                border-radius: 8px;
                padding: 0 12px;
            }
            QPushButton:hover { background: #f9fafb; border-color: #98a2b3; }
            QPushButton:focus, QComboBox:focus, QLineEdit:focus {
                border: 2px solid #d92d20;
            }
            QPushButton:disabled {
                color: #98a2b3;
                background: #f2f4f7;
                border-color: #eaecf0;
            }
            QPushButton#quietButton { color: #475467; background: transparent; }
            QPushButton#selectButton {
                color: #b42318;
                background: #fff7f6;
                border-color: #f0b5af;
                font-weight: 700;
            }
            QPushButton#selectButton:hover { background: #feeceb; }
            QPushButton#modeOption {
                color: #344054;
                background: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 9px 12px;
                text-align: left;
                font-weight: 600;
            }
            QPushButton#modeOption:hover { background: #f9fafb; border-color: #98a2b3; }
            QPushButton#modeOption:checked {
                color: #b42318;
                background: #fff7f6;
                border: 2px solid #d92d20;
            }
            QPushButton#primaryButton {
                min-height: 46px;
                color: #ffffff;
                background: #d92d20;
                border-color: #d92d20;
                padding: 0 22px;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover { background: #b42318; border-color: #b42318; }
            QPushButton#primaryButton:disabled {
                color: #ffffff;
                background: #e6aaa5;
                border-color: #e6aaa5;
            }
            QTableView {
                background: #ffffff;
                alternate-background-color: #f9fafb;
                border: 0;
                gridline-color: #eaecf0;
                selection-background-color: #fff0ef;
                selection-color: #1f2937;
            }
            QHeaderView::section {
                color: #475467;
                background: #f9fafb;
                border: 0;
                border-bottom: 1px solid #e4e7ec;
                padding: 9px;
                font-weight: 600;
            }
            QGroupBox#advancedGroup {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 12px;
            }
            QGroupBox#advancedGroup::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                font-weight: 700;
            }
            QProgressBar { max-height: 3px; border: 0; background: #eaecf0; }
            QProgressBar::chunk { background: #d92d20; }
            QPlainTextEdit {
                color: #344054;
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 8px;
                padding: 8px;
            }
            """
        )

    def _reset_interface(self) -> None:
        """恢复初始空状态，不保留任何过期的扫描或执行信息。"""

        self.results_panel.setVisible(False)
        self.details_panel.setVisible(False)
        self.details_button.setText("查看文件明细")
        self.empty_label.setVisible(True)
        self.empty_label.setText("识别速度很快，无需预先配置任何选项")
        self.scan_state_label.setVisible(True)
        self.scan_state_label.setText("等待选择文件夹")
        self.rescan_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.attention_label.setVisible(False)
        self._update_summary([])
        self._on_mode_changed()
        self._update_organize_action()
        self.choose_button.setFocus()

    def _set_busy(self, busy: bool, message: str = "", *, kind: str | None = None) -> None:
        """统一切换任务运行状态，避免重复提交。"""

        self._busy = busy
        self._busy_kind = kind if busy else None
        self.progress.setVisible(busy)
        self.path_edit.setEnabled(not busy)
        self.rescan_button.setEnabled(not busy and bool(self.path_edit.text().strip()))
        self.choose_button.setEnabled(not busy)
        self.undo_button.setEnabled(not busy)
        self.advanced_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and bool(self.results))
        self.details_button.setEnabled(not busy and bool(self.results))
        self.recursive_check.setEnabled(not busy)
        self.conflict_combo.setEnabled(not busy and not self._undo_mode)
        self.sidecar_check.setEnabled(not busy and not self._undo_mode)
        filters_enabled = (
            not busy and bool(self.results) and not self._undo_mode and not self.plan and not self._execution_completed
        )
        self._set_filter_controls_enabled(filters_enabled)
        mode = str(self.mode_combo.currentData())
        self.name_template_edit.setEnabled(not busy and not self._undo_mode and mode == "prefix")
        self.dir_template_edit.setEnabled(not busy and not self._undo_mode and mode != "prefix")
        for button in self.mode_buttons.values():
            button.setEnabled(not busy and not self._undo_mode and not self._execution_completed)
        self._update_organize_action()
        if message:
            self.scan_state_label.setText(message)

    def _set_filter_controls_enabled(self, enabled: bool) -> None:
        """统一启停筛选控件，避免出现可操作但无效果的状态。"""

        self.status_filter.setEnabled(enabled)
        self.mode_filter.setEnabled(enabled)
        self.search_edit.setEnabled(enabled)

    def _toggle_details(self) -> None:
        """展开或折叠低频使用的文件明细。"""

        visible = not self.details_panel.isVisible()
        self.details_panel.setVisible(visible)
        self.organize_card.setVisible(not visible)
        self.details_button.setText("返回整理操作" if visible else "查看文件明细")

    def _toggle_advanced(self) -> None:
        """展开或折叠高级设置。"""

        visible = not self.advanced_group.isVisible()
        self.advanced_group.setVisible(visible)
        self.advanced_button.setText("收起设置" if visible else "设置")

    def _toggle_log(self) -> None:
        """展开或折叠运行日志。"""

        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_button.setText("隐藏运行日志" if visible else "查看运行日志")

    def _on_path_changed(self) -> None:
        """目录变化时清除旧结果，避免误用已失效计划。"""

        if self._busy_kind == "execution":
            return
        if self._busy_kind == "scan":
            # 外部代码仍可能在扫描中修改文本；令旧任务失效，避免跨目录整理。
            self._scan_token += 1
            self._set_busy(False, "目录已改变，请重新识别")
        self.scan_state_label.setVisible(True)
        path_text = self.path_edit.text().strip()
        self.folder_label.setText(
            "已输入新路径 · 等待识别" if path_text else "拖入文件夹，或点击右侧按钮；选择后会立即识别"
        )
        self.folder_label.setToolTip(path_text)
        self._scan_root = None
        self._operation_root = None
        self._undo_mode = False
        self._execution_completed = False
        self.rescan_button.setEnabled(bool(path_text))
        if self.results or self.plan:
            self.results = []
            self.plan = []
            self.model.clear()
            self._update_summary([])
            self.results_panel.setVisible(False)
            self.empty_label.setVisible(True)
            self.empty_label.setText("路径已改变，按 Enter 或点击“重新识别”")
            self.scan_state_label.setText("等待重新识别")
            self.export_button.setEnabled(False)
            self.details_button.setEnabled(False)
            self.attention_label.setVisible(False)
            self._update_organize_action()
        elif not path_text:
            self.empty_label.setVisible(True)
            self.empty_label.setText("识别速度很快，无需预先配置任何选项")
            self.scan_state_label.setText("等待选择文件夹")

    def _select_mode(self, mode: str) -> None:
        """由直观的方式卡片同步内部整理模式。"""

        index = self.mode_combo.findData(mode)
        if index >= 0 and index != self.mode_combo.currentIndex():
            self.mode_combo.setCurrentIndex(index)

    def _on_recursive_changed(self, _state: int | None = None) -> None:
        """扫描范围改变后自动重新识别，避免显示与实际范围不一致。"""

        if not self._busy and self.path_edit.text().strip() and self.results:
            self.start_scan()

    def _on_mode_changed(self) -> None:
        """整理方式变化时使旧内部计划失效。"""

        mode = str(self.mode_combo.currentData())
        for value, button in self.mode_buttons.items():
            selected = value == mode
            button.setChecked(selected)
            button.setText(f"✓ {self.mode_option_texts[value]}" if selected else self.mode_option_texts[value])
        self._invalidate_plan()
        templates_enabled = mode == "prefix"
        settings_enabled = not self._busy and not self._undo_mode
        self.name_template_edit.setEnabled(settings_enabled and templates_enabled)
        self.dir_template_edit.setEnabled(settings_enabled and not templates_enabled)

    def _update_organize_action(self) -> None:
        """根据当前整理方式更新自然语言说明和唯一主按钮。"""

        mode = str(self.mode_combo.currentData())
        count = (
            sum(result.mode.value in {"dlog", "dlog2", "rec2100_hlg"} for result in self.results)
            if mode == "prefix"
            else sum(result.evidence.primary_source != "conflict" for result in self.results)
        )
        descriptions = {
            "prefix": (f"将为 {count} 个 D-Log / D-Log2 / HLG HDR 视频添加前缀。", f"添加前缀（{count}）"),
            "move": (f"将移动 {count} 个视频到分类文件夹，原位置将不再保留这些文件。", f"移动并整理（{count}）"),
            "copy": (f"将复制 {count} 个视频到分类文件夹，全部原始文件保持不变。", f"复制并整理（{count}）"),
        }
        description, button_text = descriptions[mode]
        if self._undo_mode:
            self.action_hint.setText(f"已载入操作记录，共 {len(self.plan)} 个文件可以撤销。")
            self.apply_button.setText(f"撤销这次整理（{len(self.plan)}）")
            self.apply_button.setEnabled(bool(self.plan) and not self._busy)
            self.action_status_label.setText("点击后会显示最终数量并再次确认")
            self.action_status_label.setVisible(True)
            return
        self.action_hint.setText(description if self.results else "完成识别后，这里会显示本次整理的准确说明。")
        self.apply_button.setText(button_text)
        enabled = bool(self.results) and count > 0 and not self._busy and not self._execution_completed
        self.apply_button.setEnabled(enabled)
        if self._execution_completed:
            self.action_status_label.setText("本次操作已完成；如需再次整理，请重新识别")
            self.action_status_label.setVisible(True)
        else:
            self.action_status_label.setVisible(False)

    def _invalidate_plan(self) -> None:
        """设置变化时丢弃内部计划，执行前会自动重新计算。"""

        if self._undo_mode:
            return
        had_plan = bool(self.plan)
        self.plan = []
        if had_plan and self.results and not self._busy and not self._execution_completed:
            self._apply_filters()
            self._set_filter_controls_enabled(True)
        if hasattr(self, "action_hint"):
            self._update_organize_action()

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001,N802
        """只接受包含本地目录的拖拽。"""

        if self._busy:
            return
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and Path(urls[0].toLocalFile()).is_dir():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: ANN001,N802
        """使用拖入的第一个目录并开始扫描。"""

        if self._busy:
            return
        urls = event.mimeData().urls()
        if urls:
            self.path_edit.setText(urls[0].toLocalFile())
            self.start_scan()

    def choose_directory(self) -> None:
        """选择扫描目录并立即进入扫描流程。"""

        if self._busy:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择 DJI 视频目录")
        if directory:
            self.path_edit.setText(directory)
            self.start_scan()

    def start_scan(self) -> None:
        """启动带令牌的后台扫描，旧任务结果不会覆盖新状态。"""

        if self._busy:
            return
        directory = Path(self.path_edit.text()).expanduser()
        if not directory.is_dir():
            QMessageBox.warning(self, "目录无效", f"目录不存在：{directory}")
            return
        directory = directory.resolve()
        self._scan_token += 1
        token = self._scan_token
        self._undo_mode = False
        self._execution_completed = False
        self._scan_root = directory
        self._operation_root = directory
        self._scan_recursive = self.recursive_check.isChecked()
        for combo in (self.status_filter, self.mode_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.results = []
        self.plan = []
        self.model.clear()
        self.table.setColumnHidden(3, False)
        for column in (4, 5, 6):
            self.table.setColumnHidden(column, True)
        self.results_panel.setVisible(False)
        self.details_panel.setVisible(False)
        self.organize_card.setVisible(True)
        self.details_button.setText("查看文件明细")
        self.empty_label.setVisible(True)
        self.empty_label.setText("正在读取 DJI 视频元数据…")
        self.folder_label.setText(str(directory.name or directory))
        self.folder_label.setToolTip(str(directory))
        self.scan_state_label.setVisible(True)
        self._set_busy(True, "正在快速识别…", kind="scan")
        self._log(f"开始扫描：{directory}")
        worker = ScanWorker(token, directory, self._scan_recursive)
        worker.signals.finished.connect(self.on_scan_finished)
        worker.signals.failed.connect(self.on_task_failed)
        self._active_workers[("scan", token)] = worker
        self.thread_pool.start(worker)

    @Slot(object)
    def on_scan_finished(self, payload) -> None:  # noqa: ANN001
        """只接受当前令牌对应的扫描结果。"""

        token, directory, results = payload
        self._active_workers.pop(("scan", token), None)
        if token != self._scan_token or directory != self._scan_root:
            self._log(f"忽略已过期的扫描结果：任务 {token}")
            active_scan = any(kind == "scan" for kind, _token in self._active_workers)
            if not active_scan and self._busy_kind == "scan":
                self._set_busy(False, "目录已改变，请重新识别")
            return
        self.results = list(results)
        self.plan = []
        self.model.set_results(self.results)
        self._update_summary(self.results)
        counts = "，".join(f"{key} {value}" for key, value in summarize_results(self.results).items()) or "没有视频"
        self.folder_label.setText(str(directory.name or directory))
        self.folder_label.setToolTip(str(directory))
        scope = "包含子文件夹" if self._scan_recursive else "仅当前文件夹"
        self.selection_label.setText(f"共识别 {len(self.results)} 个视频 · {scope}")
        self.empty_label.setVisible(not self.results)
        self.empty_label.setText("没有找到可识别的 DJI 视频，请选择其他文件夹")
        self.results_panel.setVisible(bool(self.results))
        self.export_button.setEnabled(bool(self.results))
        self.details_button.setEnabled(bool(self.results))
        attention = summarize_results(self.results).get("无法确认", 0)
        self.attention_label.setVisible(attention > 0)
        self.attention_label.setText(f"有 {attention} 个视频无法确认色彩模式，整理前建议查看文件明细。")
        self._set_busy(False, "识别完成" if self.results else "未找到视频")
        self.scan_state_label.setVisible(not self.results)
        self._update_organize_action()
        self._log(f"扫描完成：{len(self.results)} 个文件；{counts}")

    @Slot(object)
    def on_task_failed(self, payload) -> None:  # noqa: ANN001
        """显示后台任务错误；过期任务只记日志。"""

        kind, token, message = payload
        self._active_workers.pop((kind, token), None)
        current = (kind == "scan" and token == self._scan_token) or (
            kind == "execution" and token == self._execution_token
        )
        if not current:
            task_name = {"scan": "扫描", "execution": "执行"}.get(kind, kind)
            self._log(f"忽略已过期的{task_name}任务错误：{message}")
            return
        self._set_busy(False, "任务失败")
        self.scan_state_label.setVisible(True)
        self.empty_label.setVisible(not self.results)
        if not self.results:
            self.empty_label.setText("识别失败，请检查目录后重试")
        self._log(f"任务失败：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def _update_summary(self, results: list[ScanResult]) -> None:
        """更新四类识别结果数量。"""

        counts = summarize_results(results)
        for key, label in self.metric_labels.items():
            label.setText(str(len(results) if key == "全部" else counts.get(key, 0)))

    def _apply_filters(self) -> None:
        """按状态、色彩模式和搜索词刷新扫描结果表格。"""

        if not self.results or self._undo_mode or self.plan or self._execution_completed:
            return
        mode = self.mode_filter.currentData()
        status = self.status_filter.currentData()
        term = self.search_edit.text().strip().lower()
        filtered: list[ScanResult] = []
        for result in self.results:
            if mode != "all" and result.mode.value != mode:
                continue
            needs_attention = bool(result.error) or result.mode.value in {"unknown", "error"}
            if status == "ready" and needs_attention:
                continue
            if status == "attention" and not needs_attention:
                continue
            if term and term not in f"{result.path.name} {result.path} {result.mode.label}".lower():
                continue
            filtered.append(result)
        self.model.set_results(filtered)
        self.selection_label.setText(f"当前显示 {len(filtered)} / {len(self.results)} 个视频")

    def show_organize_page(self) -> None:
        """兼容旧调用：单页界面中直接聚焦整理主操作。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        self._undo_mode = False
        self.results_panel.setVisible(True)
        self._update_organize_action()

    def show_results_page(self) -> None:
        """兼容旧调用：单页界面中展开识别结果。"""

        self._apply_filters()

    def build_current_plan(self) -> bool:
        """执行前在内部校验设置并计算文件计划。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return False
        if self._execution_completed:
            QMessageBox.information(self, "本次操作已完成", "如需再次整理，请先重新识别当前目录。")
            return False
        directory = self._operation_root or self._scan_root
        if directory is None:
            # 兼容测试和外部集成直接注入结果的用法，同时验证结果确实属于该目录。
            candidate = Path(self.path_edit.text()).resolve()
            if candidate.is_dir() and all(result.path.resolve().is_relative_to(candidate) for result in self.results):
                directory = candidate
                self._operation_root = candidate
            else:
                QMessageBox.warning(self, "目录状态已失效", "请重新识别当前目录后再整理。")
                return False
        try:
            self.plan = build_plan(
                self.results,
                root=directory,
                mode=str(self.mode_combo.currentData()),
                conflict_policy=ConflictPolicy(str(self.conflict_combo.currentData())),
                name_template=self.name_template_edit.text().strip() or None,
                dir_template=self.dir_template_edit.text().strip() or None,
                with_sidecars=self.sidecar_check.isChecked(),
            )
        except (KeyError, ValueError, OSError) as exc:
            self.plan = []
            self.apply_button.setEnabled(False)
            self.action_hint.setText(f"设置有误：{exc}")
            QMessageBox.warning(self, "无法开始整理", str(exc))
            return False
        self.model.set_plan(self.plan)
        self._set_filter_controls_enabled(False)
        ready = sum(not item.skipped and item.target is not None for item in self.plan)
        skipped = len(self.plan) - ready
        self.action_status_label.setText(f"执行前检查：将处理 {ready} 个，跳过 {skipped} 个")
        self.action_status_label.setVisible(True)
        self._log(f"执行前检查完成：{ready} 个待处理，{skipped} 个跳过")
        return True

    def apply_current_plan(self) -> None:
        """自动计算计划，最终确认后在后台执行。"""

        if self._busy:
            return
        if not self._undo_mode:
            if not self.build_current_plan():
                return
        elif not self.plan:
            QMessageBox.information(self, "没有可撤销内容", "当前操作记录中没有可撤销的文件。")
            return
        ready = sum(not item.skipped and item.target is not None for item in self.plan)
        skipped = len(self.plan) - ready
        verb = "撤销" if self._undo_mode else str(self.mode_combo.currentText()).replace("（推荐）", "")
        answer = QMessageBox.question(
            self,
            "最终确认",
            f"整理方式：{verb}\n将处理 {ready} 个文件，跳过 {skipped} 个。\n\n确认后立即开始，是否继续？",
        )
        if answer != yes_button():
            if not self._undo_mode:
                self._invalidate_plan()
            return
        self._execution_token += 1
        token = self._execution_token
        operation = "undo" if self._undo_mode else str(self.mode_combo.currentData())
        root = self._operation_root or self._scan_root
        if root is None:
            QMessageBox.warning(self, "目录状态已失效", "请重新识别目录或重新载入操作记录。")
            return
        worker = ExecutionWorker(token, root, operation, self.plan)
        worker.signals.finished.connect(self.on_execution_finished)
        worker.signals.failed.connect(self.on_task_failed)
        self._active_workers[("execution", token)] = worker
        self._set_busy(True, "正在执行文件操作…", kind="execution")
        self.thread_pool.start(worker)

    @Slot(object)
    def on_execution_finished(self, outcome: ExecutionOutcome) -> None:
        """刷新执行结果，并立即使旧计划失效。"""

        self._active_workers.pop(("execution", outcome.token), None)
        if outcome.token != self._execution_token:
            self._log(f"忽略已过期的执行结果：任务 {outcome.token}")
            return
        self.model.set_execution_records(outcome.records)
        success = 0
        skipped = 0
        failed = 0
        for index, record in enumerate(outcome.records):
            item = self.plan[index] if index < len(self.plan) else None
            if item is not None and (item.skipped or item.target is None):
                skipped += 1
            elif record.success:
                success += 1
            else:
                failed += 1
        self.plan = []
        self._execution_completed = True
        self._set_busy(False, "执行完成")
        manifest_text = str(outcome.manifest_path) if outcome.manifest_path else "未能写入"
        self.action_hint.setText(f"整理完成：成功 {success} 个，跳过 {skipped} 个，失败 {failed} 个。")
        self.action_status_label.setText(f"操作记录：{manifest_text}")
        self.action_status_label.setVisible(True)
        self.apply_button.setEnabled(False)
        for button in self.mode_buttons.values():
            button.setEnabled(False)
        self._log(f"执行完成：成功 {success}，跳过 {skipped}，失败 {failed}；操作记录：{manifest_text}")
        if outcome.manifest_error:
            QMessageBox.warning(
                self,
                "操作已完成，但撤销记录写入失败",
                f"文件操作已经执行，请不要重复点击。撤销记录写入失败：{outcome.manifest_error}",
            )
        elif failed:
            self.table.setColumnHidden(3, True)
            self.table.setColumnHidden(6, False)
            self.details_panel.setVisible(True)
            self.organize_card.setVisible(False)
            self.details_button.setText("返回整理操作")
            QMessageBox.warning(self, "部分文件处理失败", f"成功 {success} 个，跳过 {skipped} 个，失败 {failed} 个。")
        else:
            QMessageBox.information(
                self,
                "整理完成",
                f"成功 {success} 个，跳过 {skipped} 个。\n操作记录：{manifest_text}",
            )

    def export_report(self) -> None:
        """导出当前完整扫描报告，而不是只导出筛选结果。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "导出识别报告", "dji_color_modes.csv", "CSV (*.csv);;JSON (*.json)"
        )
        if not path:
            return
        fmt = "json" if selected.startswith("JSON") or path.lower().endswith(".json") else "csv"
        try:
            write_report(self.results, Path(path), fmt=fmt)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._log(f"已导出报告：{path}")

    def undo_from_manifest(self) -> None:
        """读取操作记录并进入明确的撤销模式。"""

        if self._busy:
            return
        path, _selected = QFileDialog.getOpenFileName(self, "选择操作记录", "", "JSON (*.json)")
        if not path:
            return
        try:
            manifest = read_manifest(Path(path))
            plan = build_undo_plan(manifest.records)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "无法读取操作记录", str(exc))
            return
        self.path_edit.blockSignals(True)
        self.path_edit.setText(str(manifest.root))
        self.path_edit.blockSignals(False)
        self._undo_mode = True
        self._execution_completed = False
        self._scan_root = manifest.root
        self._operation_root = manifest.root
        self.plan = plan
        self.results = [item.scan_result for item in plan]
        self.model.set_plan(plan)
        self._update_summary(self.results)
        self.folder_label.setText(f"{manifest.root.name or manifest.root} · 已载入操作记录")
        self.folder_label.setToolTip(str(manifest.root))
        self.scan_state_label.setVisible(False)
        self.selection_label.setText(f"已从操作记录载入 {len(plan)} 个文件")
        self.results_panel.setVisible(True)
        self.empty_label.setVisible(False)
        self.attention_label.setVisible(False)
        self.details_panel.setVisible(False)
        self.organize_card.setVisible(True)
        self.details_button.setText("查看文件明细")
        self._update_organize_action()
        self._set_filter_controls_enabled(False)
        for button in self.mode_buttons.values():
            button.setEnabled(False)
        self.path_edit.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.advanced_button.setEnabled(False)
        self._log(f"已载入撤销计划：{len(plan)} 条")

    def _log(self, message: str) -> None:
        """写入中文运行日志。"""

        self.log.appendPlainText(message)


def create_window_for_smoke_test() -> MainWindow:
    """创建不进入事件循环的窗口，供 CI 冒烟测试复用。"""

    if QApplication.instance() is None:
        raise RuntimeError("必须先创建 QApplication")
    return MainWindow()
