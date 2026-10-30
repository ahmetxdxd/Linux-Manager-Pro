"""Generic table model + filter that displays thousands of rows quickly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QStyledItemDelegate


@dataclass
class Column:
    title: str
    text: Callable[[Any], str]
    sort_key: Callable[[Any], Any] | None = None
    icon: Callable[[Any], QIcon | None] | None = None
    color: Callable[[Any], str | None] | None = None
    tooltip: Callable[[Any], str] | None = None
    align_right: bool = False


SORT_ROLE = Qt.ItemDataRole.UserRole + 1
OBJECT_ROLE = Qt.ItemDataRole.UserRole + 2


class ObjectTableModel(QAbstractTableModel):
    """Each row is a Python object. Optionally a checkbox in the first column."""

    def __init__(self, columns: list[Column], checkable: bool = False, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.checkable = checkable
        self.rows: list[Any] = []
        self.checked: set[int] = set()       # the row key, not id(obj)
        self.key: Callable[[Any], Any] = id
        self.on_check_changed: Callable[[], None] | None = None

    # --- veri ---
    def set_rows(self, rows: list[Any], keep_checked: bool = False) -> None:
        if self.checkable and self.key is id:
            # id() values are reused for new objects, so a tick could jump to another row
            raise ValueError("checkable tables need a stable `key` (e.g. lambda r: r.path)")
        self.beginResetModel()
        self.rows = list(rows)
        if keep_checked:
            keys = {self.key(r) for r in self.rows}
            self.checked &= keys
        else:
            self.checked = set()
        self.endResetModel()
        self._notify()

    def obj(self, row: int) -> Any:
        return self.rows[row]

    def checked_objects(self) -> list[Any]:
        return [r for r in self.rows if self.key(r) in self.checked]

    def set_checked(self, objs, on: bool = True) -> None:
        for o in objs:
            k = self.key(o)
            if on:
                self.checked.add(k)
            else:
                self.checked.discard(k)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 0))
        self._notify()

    def _notify(self) -> None:
        if self.on_check_changed:
            self.on_check_changed()

    # --- Qt interface ---
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.checkable and index.column() == 0:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        obj = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.text(obj)
        if role == SORT_ROLE:
            return col.sort_key(obj) if col.sort_key else col.text(obj).lower()
        if role == OBJECT_ROLE:
            return obj
        if role == Qt.ItemDataRole.DecorationRole and col.icon:
            return col.icon(obj)
        if role == Qt.ItemDataRole.ForegroundRole and col.color:
            c = col.color(obj)
            return QColor(c) if c else None
        if role == Qt.ItemDataRole.ToolTipRole and col.tooltip:
            return col.tooltip(obj)
        if role == Qt.ItemDataRole.TextAlignmentRole and col.align_right:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.CheckStateRole and self.checkable and index.column() == 0:
            return (Qt.CheckState.Checked if self.key(obj) in self.checked
                    else Qt.CheckState.Unchecked)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if role == Qt.ItemDataRole.CheckStateRole and self.checkable and index.column() == 0:
            k = self.key(self.rows[index.row()])
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self.checked.add(k)
            else:
                self.checked.discard(k)
            self.dataChanged.emit(index, index, [role])
            self._notify()
            return True
        return False


class FilterProxy(QSortFilterProxyModel):
    """Defines the filter as a Python predicate; sorting uses SORT_ROLE."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.predicate: Callable[[Any], bool] | None = None
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_predicate(self, predicate: Callable[[Any], bool] | None) -> None:
        self.predicate = predicate
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802
        if self.predicate is None:
            return True
        model = self.sourceModel()
        return self.predicate(model.rows[source_row])

    def lessThan(self, left, right):  # noqa: N802
        a = left.data(SORT_ROLE)
        b = right.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def visible_objects(self) -> list[Any]:
        return [self.index(r, 0).data(OBJECT_ROLE) for r in range(self.rowCount())]


class _PaddedDelegate(QStyledItemDelegate):
    """Adds the stylesheet's cell padding to the 'resize to contents' calculation;
    otherwise columns clip the text by a few pixels."""

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        return QSize(size.width() + 18, size.height())


def make_table(model: ObjectTableModel, proxy: FilterProxy | None = None):
    from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView
    view = QTableView()
    view.setItemDelegate(_PaddedDelegate(view))
    if proxy is None:
        proxy = FilterProxy(view)
    proxy.setSourceModel(model)
    view.setModel(proxy)
    view.setSortingEnabled(True)
    view.setAlternatingRowColors(True)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(34)
    view.horizontalHeader().setHighlightSections(False)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(False)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setIconSize(QSize(22, 22))
    return view, proxy
