"""Dock panel that shows the mass properties of the selected bodies.

The panel follows the selection and refreshes after every recompute.
"""

import csv
import os

import numpy as np

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from massprops import compute

ICON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Resources", "icons", "MassProperties.svg")
DOCK_NAME = "MassProperties_Panel"
PARAMS = "User parameter:BaseApp/Preferences/Mod/MassProperties"

DENSITIES = [
    ("Steel", 7850.0),
    ("Aluminium", 2700.0),
    ("Copper", 8960.0),
    ("Brass", 8500.0),
    ("Titanium", 4500.0),
    ("ABS", 1050.0),
    ("PLA", 1240.0),
    ("Water", 1000.0),
    ("Wood (spruce)", 450.0),
]
MASS_UNITS = [("kg", 1.0), ("g", 1e3)]
LENGTH_UNITS = [("mm", 1e3), ("m", 1.0)]
CONVENTIONS = [
    ("Tensor (off-diagonal −Ixy)", "tensor"),
    ("Products of inertia (off-diagonal +Ixy)", "products"),
]

COLOR_MOMENT = "#1f6feb"
COLOR_PRODUCT = "#d9480f"
COLOR_ZERO = "#9a9a9a"


def fmt(x, zero=0.0):
    if abs(x) <= zero:
        return "0"
    return f"{x:.6g}"


def selectable(label):
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    return label


class MatrixBox(QtWidgets.QGroupBox):
    """3×3 inertia matrix with moments in blue and products in orange."""

    def __init__(self):
        super().__init__("Inertia matrix")
        grid = QtWidgets.QGridLayout(self)
        grid.setHorizontalSpacing(14)
        for k, ax in enumerate("xyz"):
            for r, c in ((0, k + 1), (k + 1, 0)):
                lab = QtWidgets.QLabel(f"<i>{ax}</i>")
                lab.setAlignment(QtCore.Qt.AlignCenter)
                lab.setStyleSheet(f"color: {COLOR_ZERO}")
                grid.addWidget(lab, r, c)
        self.cells = [[selectable(QtWidgets.QLabel()) for _ in range(3)] for _ in range(3)]
        for i in range(3):
            for j in range(3):
                self.cells[i][j].setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                grid.addWidget(self.cells[i][j], i + 1, j + 1)
        self.note = selectable(QtWidgets.QLabel())
        self.note.setWordWrap(True)
        grid.addWidget(self.note, 4, 0, 1, 4)

    def show_matrix(self, M, products):
        zero = 1e-9 * np.abs(M).max()
        for i in range(3):
            for j in range(3):
                color = COLOR_MOMENT if i == j else (COLOR_ZERO if abs(M[i, j]) <= zero else COLOR_PRODUCT)
                self.cells[i][j].setText(f"<span style='color:{color}'>{fmt(M[i, j], zero)}</span>")
        sign = 1 if products else -1  # products of inertia from the displayed matrix
        self.note.setText(
            "Products of inertia: "
            + ",&nbsp; ".join(
                f"I<sub>{n}</sub> = {fmt(sign * M[i, j], zero)}" for n, i, j in (("xy", 0, 1), ("xz", 0, 2), ("yz", 1, 2))
            )
        )

    def clear(self):
        for row in self.cells:
            for c in row:
                c.setText("–")
        self.note.setText("")


class Panel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.params = App.ParamGet(PARAMS)
        self.bodies = []  # [(document name, object name)]
        self.vertex = None  # picked point [m], global coordinates
        self.axes_doc = None  # document that holds the drawn principal axes
        self.rows = []  # last results, for copy and export
        self._drawing = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)

        lay = QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel()
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        lay.addWidget(self._settings_box())

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Body", "Density [kg/m³]", "Source", "Mass"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)

        self.summary = selectable(QtWidgets.QLabel())
        lay.addWidget(self.summary)

        self.matrix = MatrixBox()
        lay.addWidget(self.matrix)

        pbox = QtWidgets.QGroupBox("Principal moments at the center of mass")
        pl = QtWidgets.QVBoxLayout(pbox)
        self.principal = selectable(QtWidgets.QLabel())
        pl.addWidget(self.principal)
        self.draw_axes = QtWidgets.QCheckBox("Draw principal axes")
        pl.addWidget(self.draw_axes)
        lay.addWidget(pbox)

        buttons = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copy")
        export = QtWidgets.QPushButton("Export CSV…")
        buttons.addWidget(copy)
        buttons.addWidget(export)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        legend = QtWidgets.QLabel(
            f"<span style='color:{COLOR_MOMENT}'>■</span> moments of inertia &nbsp; "
            f"<span style='color:{COLOR_PRODUCT}'>■</span> products of inertia"
        )
        lay.addWidget(legend)
        lay.addStretch(1)

        copy.clicked.connect(self.copy)
        export.clicked.connect(self.export_csv)
        self.draw_axes.toggled.connect(lambda _: self.schedule())

        self.sel_observer = _SelectionObserver(self)
        self.doc_observer = _DocumentObserver(self)
        Gui.Selection.addObserver(self.sel_observer)
        App.addDocumentObserver(self.doc_observer)
        self.take_selection()

    # --- settings -----------------------------------------------------------

    def _settings_box(self):
        box = QtWidgets.QGroupBox("Settings")
        g = QtWidgets.QGridLayout(box)

        self.use_material = QtWidgets.QCheckBox("Use the density of each object's material")
        self.use_material.setChecked(self.params.GetBool("UseMaterial", True))
        g.addWidget(self.use_material, 0, 0, 1, 3)

        self.preset = QtWidgets.QComboBox()
        for name, rho in DENSITIES:
            self.preset.addItem(name, rho)
        self.preset.addItem("Custom", None)
        self.density = QtWidgets.QDoubleSpinBox()
        self.density.setRange(0.001, 30000.0)
        self.density.setDecimals(1)
        self.density.setSuffix(" kg/m³")
        self.density.setValue(self.params.GetFloat("DefaultDensity", 7850.0))
        self._sync_preset(self.density.value())
        g.addWidget(QtWidgets.QLabel("Default density"), 1, 0)
        g.addWidget(self.preset, 1, 1)
        g.addWidget(self.density, 1, 2)

        self.mass_unit = self._combo(MASS_UNITS, "MassUnit")
        self.length_unit = self._combo(LENGTH_UNITS, "LengthUnit")
        units = QtWidgets.QHBoxLayout()
        units.addWidget(self.mass_unit)
        units.addWidget(self.length_unit)
        g.addWidget(QtWidgets.QLabel("Units"), 2, 0)
        g.addLayout(units, 2, 1, 1, 2)

        self.convention = self._combo(CONVENTIONS, "Convention")
        g.addWidget(QtWidgets.QLabel("Matrix form"), 3, 0)
        g.addWidget(self.convention, 3, 1, 1, 2)

        self.frame = QtWidgets.QComboBox()
        self.pick = QtWidgets.QPushButton("Pick vertex")
        self.pick.setCheckable(True)
        self.pick.setToolTip("Click, then select a vertex in the 3D view to use it as the reference point")
        g.addWidget(QtWidgets.QLabel("Reference"), 4, 0)
        g.addWidget(self.frame, 4, 1, 1, 2)
        g.addWidget(self.pick, 5, 1)

        self.font = QtWidgets.QSpinBox()
        self.font.setRange(7, 28)
        self.font.setSuffix(" pt")
        self.font.setValue(self.params.GetInt("FontSize", 10))
        g.addWidget(QtWidgets.QLabel("Font size"), 6, 0)
        g.addWidget(self.font, 6, 1)

        self.use_material.toggled.connect(lambda v: (self.params.SetBool("UseMaterial", v), self.schedule()))
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        self.density.valueChanged.connect(self._density_typed)
        self.frame.currentIndexChanged.connect(lambda _: self.schedule())
        self.pick.toggled.connect(self._pick_toggled)
        self.font.valueChanged.connect(self._set_font)
        self._set_font(self.font.value())
        return box

    def _combo(self, items, key):
        cb = QtWidgets.QComboBox()
        for name, data in items:
            cb.addItem(name, data)
        cb.setCurrentIndex(min(self.params.GetInt(key, 0), len(items) - 1))
        cb.currentIndexChanged.connect(lambda i: (self.params.SetInt(key, i), self.schedule()))
        return cb

    def _sync_preset(self, rho):
        i = next((k for k, (_, r) in enumerate(DENSITIES) if r == rho), len(DENSITIES))
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(i)
        self.preset.blockSignals(False)

    def _preset_chosen(self, i):
        rho = self.preset.itemData(i)
        if rho is not None:
            self.density.setValue(rho)

    def _density_typed(self, rho):
        self._sync_preset(rho)
        self.params.SetFloat("DefaultDensity", rho)
        self.schedule()

    def _set_font(self, pt):
        self.params.SetInt("FontSize", pt)
        self.setStyleSheet(f"font-size: {pt}pt;")

    def _pick_toggled(self, on):
        self.pick.setText("Select a vertex…" if on else "Pick vertex")

    # --- selection ----------------------------------------------------------

    def schedule(self):
        self._timer.start(0)

    def take_selection(self):
        if self.pick.isChecked():
            for sel in Gui.Selection.getSelectionEx():
                for sub in sel.SubObjects:
                    if isinstance(sub, Part.Vertex):
                        self.vertex = np.array(sub.Point) * compute.MM
                        self.pick.setChecked(False)
                        self._update_frames(select=("vertex",))
                        self.schedule()
                        return
            return
        objs = [o for o in Gui.Selection.getSelection() if compute.has_solid(o)]
        if objs:
            self.bodies = [(o.Document.Name, o.Name) for o in objs]
        self.schedule()

    def objects(self):
        out = []
        for d, n in self.bodies:
            doc = App.listDocuments().get(d)
            o = doc.getObject(n) if doc else None
            if compute.has_solid(o):
                out.append(o)
        return out

    def _update_frames(self, select=None):
        """Rebuild the reference list: fixed choices, LCS objects, picked vertex."""
        current = select or self.frame.currentData() or ("com_global",)
        items = [
            ("Center of mass, global axes", ("com_global",)),
            ("Center of mass, principal axes", ("com_principal",)),
            ("Center of mass, body axes", ("com_body",)),
            ("Body origin, body axes", ("body",)),
            ("Global origin", ("global",)),
        ]
        doc = App.ActiveDocument
        if doc:
            for o in doc.Objects:
                if compute.is_lcs(o):
                    items.append((f"{o.Label}", ("lcs", doc.Name, o.Name)))
        if self.vertex is not None:
            items.append(("Picked vertex, global axes", ("vertex",)))
        if [self.frame.itemData(i) for i in range(self.frame.count())] == [k for _, k in items]:
            return
        self.frame.blockSignals(True)
        self.frame.clear()
        for name, key in items:
            self.frame.addItem(name, key)
        keys = [k for _, k in items]
        self.frame.setCurrentIndex(keys.index(current) if current in keys else 0)
        self.frame.blockSignals(False)

    def _frame(self, total):
        key = self.frame.currentData() or ("com_global",)
        if key[0] == "com_principal":
            return total.principal_frame()
        if key[0] == "com_body":
            return compute.Frame("Center of mass, body axes", total.com, total.frame.R)
        if key[0] == "body":
            return total.frame
        if key[0] == "global":
            return compute.Frame.global_frame()
        if key[0] == "lcs":
            doc = App.listDocuments().get(key[1])
            o = doc.getObject(key[2]) if doc else None
            if o is not None:
                return compute.Frame.from_placement(o.Label, compute.global_placement(o))
        if key[0] == "vertex" and self.vertex is not None:
            return compute.Frame("Picked vertex", self.vertex, np.eye(3))
        return compute.Frame("Center of mass, global axes", total.com, np.eye(3))

    # --- results ------------------------------------------------------------

    def refresh(self):
        self._update_frames()
        objs = self.objects()
        if not objs:
            self._show_empty()
            return
        try:
            parts = [compute.body_props(o, self.density.value(), self.use_material.isChecked()) for o in objs]
        except Exception as e:
            self.title.setText(f"Calculation failed: {e}")
            return
        total = compute.combine(parts)
        frame = self._frame(total)

        mu, mf = self.mass_unit.currentText(), self.mass_unit.currentData()
        lu, lf = self.length_unit.currentText(), self.length_unit.currentData()
        iu, i_f = f"{mu}·{lu}²", mf * lf**2
        products = self.convention.currentData() == "products"

        names = ", ".join(p.label for p in parts)
        self.title.setText(f"<b style='font-size:125%'>{names}</b>" if len(parts) == 1 else
                           f"<b style='font-size:125%'>{len(parts)} bodies</b><br>{names}")

        self.table.setRowCount(len(parts))
        for r, p in enumerate(parts):
            for c, v in enumerate([p.label, fmt(p.density), p.density_source, f"{fmt(p.mass * mf)} {mu}"]):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        self.table.setFixedHeight(self.table.horizontalHeader().height() + 4
                                  + sum(self.table.rowHeight(r) for r in range(len(parts))))

        com_g = total.com * lf
        com_f = total.com_in(frame) * lf
        vec = lambda v: "(" + ", ".join(fmt(x, 1e-12 * max(1.0, np.abs(v).max())) for x in v) + ")"
        self.summary.setText(
            f"Mass: <b>{fmt(total.mass * mf)} {mu}</b><br>"
            f"Volume: {fmt(total.volume * lf**3)} {lu}³ &nbsp; Surface area: {fmt(total.area * lf**2)} {lu}²<br>"
            f"Center of mass (global): {vec(com_g)} {lu}<br>"
            f"Center of mass (reference): {vec(com_f)} {lu}"
        )

        J = total.inertia_at(frame) * i_f
        M = J.copy()
        if products:
            M[~np.eye(3, dtype=bool)] *= -1
        self.matrix.setTitle(f"Inertia matrix [{iu}] about: {frame.name}")
        self.matrix.show_matrix(M, products)

        w, v = total.principal()
        lines = []
        for k in range(3):
            radius = np.sqrt(max(w[k], 0.0) / total.mass) * lf
            ax = ", ".join(f"{x + 0.0:+.4f}" for x in np.round(v[:, k], 6))
            lines.append(f"I<sub>{k + 1}</sub> = {fmt(w[k] * i_f)} {iu} &nbsp; k<sub>{k + 1}</sub> = {fmt(radius)} {lu}"
                         f"<br>&nbsp;&nbsp;&nbsp;axis {k + 1} (global): ({ax})")
        self.principal.setText("<br>".join(lines))

        self.rows = self._rows(parts, total, frame, J, M, w, v, mu, mf, lu, lf, iu, i_f, products)
        self._axes(objs[0].Document, total)

    def _rows(self, parts, total, frame, J, M, w, v, mu, mf, lu, lf, iu, i_f, products):
        rows = [("Bodies", ", ".join(p.label for p in parts), "")]
        for p in parts:
            rows.append((f"Density {p.label}", fmt(p.density), f"kg/m³ ({p.density_source})"))
        rows += [
            ("Mass", fmt(total.mass * mf), mu),
            ("Volume", fmt(total.volume * lf**3), f"{lu}³"),
            ("Surface area", fmt(total.area * lf**2), f"{lu}²"),
        ]
        for name, c in (("global", total.com), ("reference", total.com_in(frame))):
            for ax, x in zip("xyz", c * lf):
                rows.append((f"Center of mass {ax} ({name})", fmt(x), lu))
        rows.append(("Reference", frame.name, ""))
        rows.append(("Matrix form", self.convention.currentText(), ""))
        zero = 1e-9 * np.abs(M).max()
        for i, a in enumerate("xyz"):
            for j, b in enumerate("xyz"):
                rows.append((f"Matrix {a}{b}", fmt(M[i, j], zero), iu))
        for k in range(3):
            rows.append((f"Principal moment I{k + 1}", fmt(w[k] * i_f), iu))
            rows.append((f"Radius of gyration k{k + 1}", fmt(np.sqrt(max(w[k], 0.0) / total.mass) * lf), lu))
            for ax, x in zip("xyz", v[:, k]):
                rows.append((f"Principal axis {k + 1} {ax} (global)", f"{x:.6f}", ""))
        return rows

    def _axes(self, doc, total):
        self._drawing = True
        try:
            if self.axes_doc and (self.axes_doc != doc.Name or not self.draw_axes.isChecked()):
                old = App.listDocuments().get(self.axes_doc)
                if old:
                    compute.remove_principal_axes(old)
                self.axes_doc = None
            if self.draw_axes.isChecked():
                bb = App.BoundBox()
                for o in self.objects():
                    bb.add(o.Shape.BoundBox)
                compute.draw_principal_axes(doc, total, 1.2 * bb.DiagonalLength)
                self.axes_doc = doc.Name
        finally:
            self._drawing = False

    def _show_empty(self):
        self.title.setText("Select one or more solids in the 3D view or the tree.")
        self.table.setRowCount(0)
        self.summary.setText("")
        self.matrix.clear()
        self.principal.setText("")
        self.rows = []

    def copy(self):
        text = "\n".join("\t".join(r) for r in self.rows)
        QtWidgets.QApplication.clipboard().setText(text)

    def export_csv(self):
        if not self.rows:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export mass properties", "mass_properties.csv",
                                                        "CSV files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["quantity", "value", "unit"])
                w.writerows(self.rows)

    def shutdown(self):
        Gui.Selection.removeObserver(self.sel_observer)
        App.removeDocumentObserver(self.doc_observer)


class _SelectionObserver:
    def __init__(self, panel):
        self.p = panel

    def addSelection(self, doc, obj, sub, pnt):
        QtCore.QTimer.singleShot(0, self.p.take_selection)

    def setSelection(self, doc):
        QtCore.QTimer.singleShot(0, self.p.take_selection)


class _DocumentObserver:
    def __init__(self, panel):
        self.p = panel

    def slotRecomputedDocument(self, doc):
        if not self.p._drawing:
            self.p.schedule()

    def slotDeletedDocument(self, doc):
        if self.p.axes_doc == doc.Name:
            self.p.axes_doc = None
        self.p.schedule()

    def slotActivateDocument(self, doc):
        self.p.schedule()


_panel = None


def toggle():
    """Show the panel, or hide it if it is already visible."""
    mw = Gui.getMainWindow()
    global _panel
    dock = mw.findChild(QtWidgets.QDockWidget, DOCK_NAME)
    if dock is not None and _panel is not None:
        dock.setVisible(not dock.isVisible())
        if dock.isVisible():
            _panel.take_selection()
        return
    dock = QtWidgets.QDockWidget("Mass Properties", mw)
    dock.setObjectName(DOCK_NAME)
    panel = Panel()
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel)
    dock.setWidget(scroll)
    _panel = panel
    dock.destroyed.connect(lambda *_: panel.shutdown())
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
