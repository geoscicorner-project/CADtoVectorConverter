# -*- coding: utf-8 -*-
"""CADtoVectorConverter - QGIS plugin.

Reads DXF through QGIS' GDAL/OGR provider, groups entities by geometry,
keeps the original CAD layer name in CAD_LAYER, and writes GIS formats.
"""
import os
import re
import csv
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QFont
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QFileDialog, QLabel, QComboBox, QCheckBox, QLineEdit, QGroupBox,
    QMessageBox, QProgressBar, QPlainTextEdit, QAbstractItemView,
    QWidget, QSizePolicy, QLayout
)
from qgis.gui import QgsProjectionSelectionDialog
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsFields, QgsField, QgsGeometry,
    QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsFeatureRequest, QgsApplication
)
from qgis.PyQt.QtCore import QVariant


class CrsSelectorComboBox(QComboBox):
    """CRS field that reliably opens the native QGIS CRS selector on click."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selector_callback = None

    def showPopup(self):
        # A one-item QComboBox may not emit activated() when its placeholder
        # is selected again. Opening the dialog here avoids that limitation.
        if self.isEnabled() and self.selector_callback:
            self.selector_callback()


class CadToVectorConverter:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "CADtoVectorConverter", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&CADtoVectorConverter", self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&CADtoVectorConverter", self.action)

    def run(self):
        if self.dialog is None:
            self.dialog = ConverterDialog(self.iface)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


class ConverterDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("CADtoVectorConverter")
        self.setMinimumWidth(610)
        self.setMinimumHeight(760)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        input_group = QGroupBox("1. Input DXF")
        input_layout = QVBoxLayout(input_group)
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        input_layout.addWidget(self.files)
        row = QHBoxLayout()
        add_btn = QPushButton("Add DXF files…")
        add_btn.clicked.connect(self.add_files)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.files.clear)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(clear_btn)
        input_layout.addLayout(row)
        root.addWidget(input_group)

        # CRS controls use two independent columns.  The group box height is
        # determined by the actual child layouts; no manual geometry or
        # hard-coded group height is used.
        crs_group = QGroupBox("2. Coordinate Reference System")
        crs_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        crs_outer_layout = QVBoxLayout(crs_group)
        crs_outer_layout.setContentsMargins(12, 12, 12, 12)
        crs_outer_layout.setSpacing(8)

        columns_widget = QWidget(crs_group)
        crs_layout = QHBoxLayout(columns_widget)
        crs_layout.setContentsMargins(0, 0, 0, 0)
        crs_layout.setSpacing(18)

        left_column = QWidget(columns_widget)
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        right_column = QWidget(columns_widget)
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.crs_mode = QComboBox(left_column)
        self.crs_mode.addItem("Referenced CRS (EPSG / projected CRS)", "referenced")
        self.crs_mode.addItem("Local CRS (unreferenced coordinates)", "local")
        self.crs_mode.currentIndexChanged.connect(self._toggle_crs_mode)
        self.mode_label = QLabel("Coordinate Mode:", left_column)
        self.mode_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        left_layout.addWidget(self.mode_label)
        left_layout.addWidget(self.crs_mode)

        self.source_crs_value = QgsCoordinateReferenceSystem()
        self.source_crs_display = CrsSelectorComboBox(left_column)
        self.source_crs_display.addItem("Select Source CRS...")
        self.source_crs_display.selector_callback = self._choose_source_crs
        self.source_crs_label = QLabel(
            "Source CRS (Required in Referenced Mode):", left_column
        )
        self.source_crs_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        left_layout.addSpacing(4)
        left_layout.addWidget(self.source_crs_label)
        left_layout.addWidget(self.source_crs_display)

        left_layout.addStretch(1)

        self.target_crs_value = QgsProject.instance().crs()
        self.target_crs_label = QLabel("Target CRS:", right_column)
        self.target_crs_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        right_layout.addWidget(self.target_crs_label)

        self.same_crs = QCheckBox("Use the same CRS as source", right_column)
        self.same_crs.setChecked(True)
        self.same_crs.toggled.connect(self._toggle_target_crs)
        right_layout.addWidget(self.same_crs)

        self.target_crs_display = CrsSelectorComboBox(right_column)
        self.target_crs_display.addItem(self._crs_description(self.target_crs_value))
        self.target_crs_display.selector_callback = self._choose_target_crs
        self.target_crs_display.setEnabled(False)
        right_layout.addWidget(self.target_crs_display)
        right_layout.addStretch(1)

        crs_layout.addWidget(left_column, 1)
        crs_layout.addWidget(right_column, 1)
        crs_outer_layout.addWidget(columns_widget)

        # Full-width note below both columns prevents clipping in Local mode.
        self.local_crs_note = QLabel(
            "Local CRS: X/Y/Z values will be preserved as-is. No geographic "
            "reference or coordinate transformation will be applied.", crs_group
        )
        self.local_crs_note.setWordWrap(True)
        self.local_crs_note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Apply a smaller font directly to this QLabel. Using QFont as well as
        # a widget-local stylesheet prevents the QGIS global UI font from
        # making this explanatory note as large as the main form labels.
        note_font = QFont(self.local_crs_note.font())
        note_font.setPointSizeF(7.0)
        self.local_crs_note.setFont(note_font)
        self.local_crs_note.setStyleSheet("color: #8a6d3b; font-size: 7pt;")
        self.local_crs_note.setVisible(False)
        crs_outer_layout.addWidget(self.local_crs_note)
        root.addWidget(crs_group)

        geom_group = QGroupBox("3. Geometry handling")
        geom_layout = QVBoxLayout(geom_group)
        self.closed_polygon = QCheckBox("Convert closed polylines to polygons")
        self.closed_polygon.setChecked(True)
        self.circle_polygon = QCheckBox("Convert circles/ellipses to polygons")
        self.circle_polygon.setChecked(True)
        self.text_points = QCheckBox("Keep TEXT / MTEXT entities as point features when provided by GDAL")
        self.text_points.setChecked(True)
        self.include_z = QCheckBox("Preserve Z dimension when available")
        self.include_z.setChecked(True)
        self.load_results = QCheckBox("Load output layers into the current QGIS project")
        self.load_results.setChecked(True)
        for widget in (self.closed_polygon, self.circle_polygon, self.text_points,
                       self.include_z, self.load_results):
            geom_layout.addWidget(widget)
        root.addWidget(geom_group)

        output_group = QGroupBox("4. Output")
        output_layout = QVBoxLayout(output_group)
        row = QHBoxLayout()
        row.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("GeoPackage (*.gpkg)", "GPKG")
        self.format_combo.addItem("ESRI Shapefile (*.shp)", "ESRI Shapefile")
        self.format_combo.addItem("GeoJSON (*.geojson)", "GeoJSON")
        self.format_combo.addItem("KML (*.kml)", "KML")
        row.addWidget(self.format_combo, 1)
        output_layout.addLayout(row)
        row2 = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Choose output folder…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_output)
        row2.addWidget(self.output_dir, 1)
        row2.addWidget(browse)
        output_layout.addLayout(row2)
        self.overwrite = QCheckBox("Overwrite existing output files")
        self.overwrite.setChecked(False)
        output_layout.addWidget(self.overwrite)
        root.addWidget(output_group)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Conversion log and summary will appear here…")
        self.log.setMaximumBlockCount(3000)
        root.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        self.run_btn = QPushButton("Convert")
        self.run_btn.clicked.connect(self.convert)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    @staticmethod
    def _crs_description(crs):
        if not crs or not crs.isValid():
            return "Select CRS..."
        authid = crs.authid()
        description = crs.description()
        return "{} - {}".format(authid, description) if authid else description

    def _select_crs(self, current_crs, title):
        dialog = QgsProjectionSelectionDialog(self)
        dialog.setWindowTitle(title)
        if current_crs and current_crs.isValid():
            dialog.setCrs(current_crs)
        if dialog.exec_():
            selected = dialog.crs()
            if selected and selected.isValid():
                return selected
        return None

    def _choose_source_crs(self):
        selected = self._select_crs(self.source_crs_value, "Select Source CRS")
        if selected:
            self.source_crs_value = selected
            self.source_crs_display.clear()
            self.source_crs_display.addItem(self._crs_description(selected))

    def _choose_target_crs(self):
        selected = self._select_crs(self.target_crs_value, "Select Target CRS")
        if selected:
            self.target_crs_value = selected
            self.target_crs_display.clear()
            self.target_crs_display.addItem(self._crs_description(selected))

    def _toggle_target_crs(self, checked):
        is_local = self.crs_mode.currentData() == "local"
        enabled = (not checked) and (not is_local)
        self.target_crs_display.setEnabled(enabled)

    def _toggle_crs_mode(self, index):
        is_local = self.crs_mode.currentData() == "local"
        self.source_crs_label.setEnabled(not is_local)
        self.source_crs_display.setEnabled(not is_local)
        self.local_crs_note.setVisible(is_local)
        self.target_crs_display.setEnabled((not is_local) and (not self.same_crs.isChecked()))
        if hasattr(self, "local_crs_note"):
            self.local_crs_note.updateGeometry()
            self.local_crs_note.parentWidget().updateGeometry()
            self.layout().activate()
        self.target_crs_label.setEnabled(not is_local)
        self.same_crs.setEnabled(not is_local)
        self._update_format_availability()

    def _update_format_availability(self):
        """Avoid formats whose specifications require geographic coordinates in local mode."""
        is_local = self.crs_mode.currentData() == "local"
        model = self.format_combo.model()
        for index in range(self.format_combo.count()):
            driver = self.format_combo.itemData(index)
            item = model.item(index)
            if item is not None:
                item.setEnabled(not (is_local and driver in ("GeoJSON", "KML")))
        if is_local and self.format_combo.currentData() in ("GeoJSON", "KML"):
            self.format_combo.setCurrentIndex(0)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select DXF files", "", "DXF files (*.dxf);;All files (*.*)")
        existing = {self.files.item(i).text() for i in range(self.files.count())}
        for path in paths:
            if path not in existing:
                self.files.addItem(path)

    def remove_selected(self):
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.output_dir.setText(folder)

    def _log(self, message):
        self.log.appendPlainText(message)
        self.iface.mainWindow().statusBar().showMessage(message, 5000)

    def convert(self):
        paths = [self.files.item(i).text() for i in range(self.files.count())]
        if not paths:
            QMessageBox.warning(self, "Input required", "Add at least one DXF file.")
            return
        local_mode = self.crs_mode.currentData() == "local"
        if local_mode:
            # Invalid CRS is intentional: it represents unreferenced local coordinates.
            source_crs = QgsCoordinateReferenceSystem()
            target_crs = QgsCoordinateReferenceSystem()
            if self.format_combo.currentData() in ("GeoJSON", "KML"):
                QMessageBox.warning(
                    self, "Unsupported format for Local CRS",
                    "Local CRS coordinates should not be written as GeoJSON or KML, "
                    "because those formats carry geographic-coordinate expectations. "
                    "Choose GeoPackage or Shapefile instead."
                )
                return
        else:
            source_crs = self.source_crs_value
            if not source_crs.isValid():
                QMessageBox.warning(self, "CRS required", "Select a valid source CRS before conversion.")
                return
            target_crs = source_crs if self.same_crs.isChecked() else self.target_crs_value
            if not target_crs.isValid():
                QMessageBox.warning(self, "CRS required", "Select a valid target CRS.")
                return
        out_dir = self.output_dir.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            QMessageBox.warning(self, "Output required", "Choose an existing output folder.")
            return

        self.run_btn.setEnabled(False)
        self.progress.setMaximum(len(paths))
        self.progress.setValue(0)
        self.log.clear()
        total_ok = 0
        total_failed = 0
        try:
            for index, path in enumerate(paths, 1):
                self._log("\nProcessing: " + path)
                self._log("Coordinate mode: " + ("LOCAL / UNREFERENCED" if local_mode else "REFERENCED"))
                try:
                    stats = self._convert_one(path, out_dir, source_crs, target_crs, local_mode)
                    total_ok += stats.get("written", 0)
                    total_failed += stats.get("failed", 0)
                except Exception as exc:
                    total_failed += 1
                    self._log("ERROR: {}".format(exc))
                    self._log(traceback.format_exc())
                self.progress.setValue(index)
                from qgis.PyQt.QtWidgets import QApplication
                QApplication.processEvents()
        finally:
            self.run_btn.setEnabled(True)
        self._log("\n=== BATCH SUMMARY ===")
        self._log("Output layers/files created: {}".format(total_ok))
        self._log("Files/features reported as failed: {}".format(total_failed))
        QMessageBox.information(self, "Conversion finished", "Conversion process finished. See the log for details.")

    def _convert_one(self, path, out_dir, source_crs, target_crs, local_mode=False):
        base = os.path.splitext(os.path.basename(path))[0]
        # GDAL's DXF driver exposes the drawing as an 'entities' layer.
        src = QgsVectorLayer(path, base + " [DXF]", "ogr")
        if not src.isValid():
            raise RuntimeError("QGIS/GDAL could not open this DXF. Check that the GDAL DXF driver is available and the file is readable.")

        src_fields = src.fields()
        field_names = [f.name() for f in src_fields]
        layer_idx = self._field_index(field_names, ["Layer", "CAD_LAYER", "layer"])
        type_idx = self._field_index(field_names, ["SubClasses", "EntityType", "Entity", "CAD_TYPE"])
        handle_idx = self._field_index(field_names, ["EntityHandle", "Handle", "CAD_HANDLE"])
        text_idx = self._field_index(field_names, ["Text", "TextValue", "CAD_TEXT"])
        elevation_idx = self._field_index(field_names, ["Elevation", "ELEVATION"])

        groups = {"point": [], "line": [], "polygon": []}
        counts = {"read": 0, "point": 0, "line": 0, "polygon": 0, "failed": 0, "skipped": 0}
        transform = None
        if not local_mode and source_crs != target_crs:
            transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())

        for feature in src.getFeatures():
            counts["read"] += 1
            try:
                geom = QgsGeometry(feature.geometry())
                if geom.isNull() or geom.isEmpty():
                    counts["skipped"] += 1
                    continue
                cad_layer = self._attr(feature, layer_idx, "0")
                cad_type = self._attr(feature, type_idx, "")
                cad_handle = self._attr(feature, handle_idx, "")
                cad_text = self._attr(feature, text_idx, "")
                elevation = self._attr(feature, elevation_idx, None)
                cad_type_upper = str(cad_type).upper()
                if not self.text_points.isChecked() and ("TEXT" in cad_type_upper or "MTEXT" in cad_type_upper):
                    counts["skipped"] += 1
                    continue

                # OGR normally translates LINE/POLYLINE/LWPOLYLINE to line strings.
                # Closed linework can be promoted to a polygon when requested.
                if self.closed_polygon.isChecked() and self._looks_closed(geom) and QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.LineGeometry:
                    candidate = self._line_to_polygon(geom)
                    if candidate and not candidate.isEmpty() and candidate.isGeosValid():
                        geom = candidate

                # Circles and ellipses are tessellated by GDAL as linestrings.
                if self.circle_polygon.isChecked() and QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.LineGeometry:
                    if "CIRCLE" in str(cad_type).upper() or "ELLIPSE" in str(cad_type).upper():
                        candidate = self._line_to_polygon(geom)
                        if candidate and candidate.isGeosValid():
                            geom = candidate

                gtype = QgsWkbTypes.geometryType(geom.wkbType())
                if gtype == QgsWkbTypes.PointGeometry:
                    key = "point"
                elif gtype == QgsWkbTypes.LineGeometry:
                    key = "line"
                elif gtype == QgsWkbTypes.PolygonGeometry:
                    key = "polygon"
                else:
                    counts["skipped"] += 1
                    continue

                if transform:
                    geom.transform(transform)

                attributes = [cad_layer, cad_type, cad_handle, cad_text, elevation, os.path.basename(path)]
                # Preserve the original OGR attributes as source fields too.
                for i in range(len(src_fields)):
                    value = feature.attribute(i)
                    attributes.append(value)
                groups[key].append((geom, attributes))
                counts[key] += 1
            except Exception as exc:
                counts["failed"] += 1
                self._log("Feature {} skipped: {}".format(feature.id(), exc))

        self._log("Read: {read} | Point: {point} | Line: {line} | Polygon: {polygon} | Skipped: {skipped} | Failed: {failed}".format(**counts))
        if not any(groups.values()):
            self._log("No convertible geometries found in this DXF.")
            return {"written": 0, "failed": counts["failed"]}

        driver = self.format_combo.currentData()
        written = 0
        gpkg_created_this_run = False
        for key in ("point", "line", "polygon"):
            records = groups[key]
            if not records:
                continue
            out_path, layer_name = self._output_target(out_dir, base, key, driver)
            if (os.path.exists(out_path) and not self.overwrite.isChecked()
                    and not (driver == "GPKG" and gpkg_created_this_run)):
                self._log("Skipped existing output (overwrite disabled): " + out_path)
                continue
            if driver == "GPKG":
                self._write_gpkg(records, out_path, layer_name, key, target_crs, src_fields)
                gpkg_created_this_run = True
            else:
                self._write_single(records, out_path, layer_name, key, driver, target_crs, src_fields)
            written += 1
            self._log("Created: {} ({} features)".format(out_path, len(records)))
            if self.load_results.isChecked():
                uri = out_path + "|layername=" + layer_name if driver == "GPKG" else out_path
                result = QgsVectorLayer(uri, base + "_" + key, "ogr")
                if result.isValid():
                    QgsProject.instance().addMapLayer(result)
                else:
                    self._log("Warning: output created but could not be loaded into QGIS: " + uri)
        return {"written": written, "failed": counts["failed"]}

    def _field_index(self, names, candidates):
        lower = {name.lower(): i for i, name in enumerate(names)}
        for candidate in candidates:
            if candidate.lower() in lower:
                return lower[candidate.lower()]
        return -1

    def _attr(self, feature, index, default):
        if index < 0:
            return default
        value = feature.attribute(index)
        return default if value is None else value

    def _looks_closed(self, geom):
        try:
            if geom.isMultipart():
                parts = geom.asMultiPolyline()
                return bool(parts) and all(len(p) >= 4 and p[0] == p[-1] for p in parts)
            points = geom.asPolyline()
            return len(points) >= 4 and points[0] == points[-1]
        except Exception:
            return False

    def _line_to_polygon(self, geom):
        try:
            if geom.isMultipart():
                rings = geom.asMultiPolyline()
                polygons = []
                for ring in rings:
                    if len(ring) >= 4 and ring[0] == ring[-1]:
                        polygons.append([ring])
                if not polygons:
                    return None
                # Preserve Z where present and construct a true MultiPolygon geometry.
                return QgsGeometry.fromMultiPolygon(polygons)
            ring = geom.asPolyline()
            if len(ring) >= 4 and ring[0] == ring[-1]:
                return QgsGeometry.fromPolygon([ring])
        except Exception:
            return None
        return None

    def _output_target(self, out_dir, base, key, driver):
        ext = {"GPKG": ".gpkg", "ESRI Shapefile": ".shp", "GeoJSON": ".geojson", "KML": ".kml"}[driver]
        if driver == "GPKG":
            return os.path.join(out_dir, base + ".gpkg"), base + "_" + key
        return os.path.join(out_dir, base + "_" + key + ext), base + "_" + key

    def _make_memory_layer(self, records, key, crs, src_fields):
        suffix = "Z" if self.include_z.isChecked() and any(r[0].wkbType() in (QgsWkbTypes.PointZ, QgsWkbTypes.LineStringZ, QgsWkbTypes.PolygonZ, QgsWkbTypes.MultiPointZ, QgsWkbTypes.MultiLineStringZ, QgsWkbTypes.MultiPolygonZ) for r in records) else ""
        geom_name = {"point": "Point", "line": "MultiLineString", "polygon": "MultiPolygon"}[key]
        if crs.isValid():
            uri = "{}{}?crs={}".format(geom_name, suffix, crs.authid())
        else:
            # An omitted CRS parameter creates a genuinely unreferenced memory layer.
            uri = "{}{}".format(geom_name, suffix)
        layer = QgsVectorLayer(uri, key, "memory")
        provider = layer.dataProvider()
        fields = QgsFields()
        fields.append(QgsField("CAD_LAYER", QVariant.String, len=254))
        fields.append(QgsField("CAD_TYPE", QVariant.String, len=100))
        fields.append(QgsField("CAD_HANDLE", QVariant.String, len=100))
        fields.append(QgsField("CAD_TEXT", QVariant.String, len=2000))
        fields.append(QgsField("ELEVATION", QVariant.Double))
        fields.append(QgsField("SOURCE_DXF", QVariant.String, len=254))
        used = {f.name().lower() for f in fields}
        source_names = []
        for index, field in enumerate(src_fields):
            raw = re.sub(r"[^A-Za-z0-9_]", "_", field.name())
            name = ("SRC_" + raw)[:60] or "SRC_FIELD_{}".format(index)
            candidate = name
            counter = 1
            while candidate.lower() in used:
                candidate = (name[:55] + "_{}".format(counter))[:60]
                counter += 1
            used.add(candidate.lower())
            source_names.append(candidate)
            fields.append(QgsField(candidate, QVariant.String, len=2000))
        provider.addAttributes(list(fields))
        layer.updateFields()
        features = []
        for geom, attrs in records:
            feature = QgsFeature(layer.fields())
            output_geom = QgsGeometry(geom)
            if key == "point" and output_geom.isMultipart():
                pass
            elif key in ("line", "polygon") and not output_geom.isMultipart():
                output_geom.convertToMultiType()
            feature.setGeometry(output_geom)
            safe_attrs = []
            for value in attrs:
                if value is None:
                    safe_attrs.append(None)
                elif isinstance(value, (int, float, str, bool)):
                    safe_attrs.append(value if not isinstance(value, str) or len(value) <= 2000 else value[:2000])
                else:
                    safe_attrs.append(str(value)[:2000])
            feature.setAttributes(safe_attrs)
            features.append(feature)
        provider.addFeatures(features)
        layer.updateExtents()
        return layer

    def _write_gpkg(self, records, path, layer_name, key, crs, src_fields):
        exists = os.path.exists(path)
        if exists and self.overwrite.isChecked():
            # Preserve other geometry layers in this GeoPackage; replace only this target layer.
            pass
        layer = self._make_memory_layer(records, key, crs, src_fields)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.fileEncoding = "UTF-8"
        options.layerName = layer_name
        options.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if exists
                                         else QgsVectorFileWriter.CreateOrOverwriteFile)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsProject.instance().transformContext(), options)
        if result[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError("GeoPackage writer error: {}".format(result[1]))

    def _write_single(self, records, path, layer_name, key, driver, crs, src_fields):
        if os.path.exists(path):
            if not self.overwrite.isChecked():
                raise RuntimeError("Output exists: " + path)
            if driver == "ESRI Shapefile":
                QgsVectorFileWriter.deleteShapeFile(path)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
        layer = self._make_memory_layer(records, key, crs, src_fields)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        options.layerName = layer_name
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsProject.instance().transformContext(), options)
        if result[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError("{} writer error: {}".format(driver, result[1]))
