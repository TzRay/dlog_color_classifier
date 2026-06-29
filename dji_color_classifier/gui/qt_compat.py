"""Qt 绑定兼容层。

项目发布首选 PySide6；开发和验收环境如果只有 PyQt5，也可以运行同一套 GUI。
"""

from __future__ import annotations

import os

QT_BINDING = ""


def _load_pyside6() -> bool:
    """尝试加载 PySide6。"""

    global QT_BINDING
    global QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    global QAction, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView
    global QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QTableView
    global QToolBar, QVBoxLayout, QWidget

    from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QTableView,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )

    QT_BINDING = "PySide6"
    return True


def _load_pyqt5() -> bool:
    """尝试加载 PyQt5。"""

    global QT_BINDING
    global QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    global QAction, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView
    global QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QTableView
    global QToolBar, QVBoxLayout, QWidget

    from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
    from PyQt5.QtWidgets import (
        QAction,
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QTableView,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )

    Signal = pyqtSignal
    Slot = pyqtSlot
    QT_BINDING = "PyQt5"
    return True


def _load_qt_binding() -> None:
    """按优先级加载 Qt 绑定。"""

    preferred = os.environ.get("DJI_COLOR_QT_BINDING", "PySide6").lower()
    loaders = [_load_pyside6, _load_pyqt5] if preferred != "pyqt5" else [_load_pyqt5, _load_pyside6]
    last_error: Exception | None = None
    for loader in loaders:
        try:
            loader()
            return
        except ImportError as exc:
            last_error = exc
    raise RuntimeError("未安装 PySide6 或 PyQt5，无法启动 GUI。请安装 GUI 依赖或使用 CLI。") from last_error


_load_qt_binding()


def display_role() -> int:
    """返回 Qt DisplayRole，兼容 Qt5/Qt6。"""

    return getattr(getattr(Qt, "ItemDataRole", Qt), "DisplayRole")


def horizontal_orientation() -> int:
    """返回 Qt Horizontal，兼容 Qt5/Qt6。"""

    return getattr(getattr(Qt, "Orientation", Qt), "Horizontal")


def resize_to_contents() -> int:
    """返回表头 ResizeToContents 枚举，兼容 Qt5/Qt6。"""

    return getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "ResizeToContents")


def yes_button() -> int:
    """返回 QMessageBox.Yes，兼容 Qt5/Qt6。"""

    return getattr(getattr(QMessageBox, "StandardButton", QMessageBox), "Yes")
