# CADtoVectorConverter

A QGIS plugin for batch conversion of DXF CAD drawings into GIS vector datasets using the GDAL/OGR DXF driver bundled with QGIS. The plugin groups entities by geometry type, preserves CAD layer names in the `CAD_LAYER` attribute, and provides explicit coordinate reference system handling.

> **Current input support:** DXF. The plugin name is CADtoVectorConverter, but this release currently reads DXF files only.

## Features

- Batch input of one or more DXF files.
- **Referenced CRS** mode: choose the source CRS and either retain it or transform to a target CRS.
- **Local CRS** mode: preserve drawing X/Y/Z values as-is without assigning a geographic reference or applying a coordinate transformation.
- Geometry handling options:
  - Convert closed polylines to polygons.
  - Convert circles/ellipses to polygons where supported by the entity data.
  - Keep TEXT/MTEXT entities as point features when exposed by GDAL.
  - Preserve Z dimensions when available.
- Output formats: GeoPackage, ESRI Shapefile, GeoJSON, and KML. GeoJSON and KML are disabled in Local CRS mode because arbitrary engineering coordinates should not be written as geographic data.
- Optional loading of converted layers into the current QGIS project.
- Conversion progress and detailed log.

## Requirements

- QGIS 3.28 or later.
- GDAL/OGR DXF driver available in the QGIS installation.
- No additional Python packages are required beyond the QGIS Python environment.

## Installation from ZIP

1. Download `CADtoVectorConverter_v1.1.6.zip` from the repository Releases page (or use the plugin ZIP attached to the release).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the ZIP file and install it.
4. Enable **CADtoVectorConverter** in the Installed tab if it is not enabled automatically.

Alternatively, extract the `CADtoVectorConverter` folder into the active QGIS profile's `python/plugins` directory and restart QGIS.

## Usage

1. Open **CADtoVectorConverter** from the Plugins menu or its toolbar icon.
2. Add one or more DXF files.
3. Select a coordinate mode:
   - **Referenced CRS:** select the source CRS. Select *Use the same CRS as source* to retain it, or clear the checkbox and select a target CRS to reproject.
   - **Local CRS:** use this for drawing/design coordinates that are not tied to a geographic or projected CRS. Coordinate values are preserved as-is.
4. Configure geometry handling options.
5. Choose an output format and an existing output folder.
6. Click **Convert** and review the conversion log.

## Coordinate reference system behavior

- Local mode intentionally uses an unset/invalid CRS; it is not EPSG:4326.
- No coordinate transformation is performed in Local mode.
- GeoPackage and Shapefile are available in Local mode.
- GeoJSON and KML are disabled in Local mode because these formats are generally interpreted as geographic-coordinate data.
- GeoPackage output in Local mode is written without an assigned geographic CRS.
- Shapefile output in Local mode should not receive a `.prj` file when the output CRS is unset.

## Known limitations

- DXF reading depends on the GDAL/OGR DXF driver available in the QGIS installation.
- The GDAL DXF driver exposes drawing content through an OGR `entities` layer. CAD layer names are preserved in `CAD_LAYER`.
- GDAL may translate curves (including circles, arcs, ellipses, and splines) into line strings. Circle/ellipse conversion to polygons is heuristic and depends on entity metadata.
- Closed linework is promoted to a simple polygon ring. Interior rings/holes and complex nested boundaries are not reconstructed in this release.
- Block INSERT handling follows GDAL's DXF driver behavior; the plugin does not independently expand block definitions.
- Unsupported or empty geometries are reported in the log. Some ACIS-based 3D solids/surfaces may not be readable by GDAL.
- Always test on copies of production data and verify coordinate values, geometry counts, Z values, attributes, and CRS behavior before operational use.

## Development / repository structure

The repository root is also the QGIS plugin directory. `metadata.txt` and `__init__.py` must remain at the repository root for QGIS plugin packaging workflows.

```text
(repository root / plugin package)
├── .gitignore
├── README.md
├── __init__.py
├── cad_to_vector_converter.py
├── icon.png
└── metadata.txt
```

## Version history

### 1.1.6
- Renamed the QGIS plugin to **CADtoVectorConverter**.
- Updated the Python module/class names and repository metadata for the new plugin identity.
- Preserved the v1.1.5 interface and conversion behavior, including the 7 pt Local CRS explanatory note.

### 1.1.5
- Reduced the Local CRS explanatory note font to 7 pt without changing the CRS layout or conversion workflow.

### 1.1.4
- Reduced the Local CRS explanatory note font to 9 pt.

### 1.1.3
- Fixed CRS selector activation and Local CRS note layout.

### 1.1.2
- CRS fields open the native QGIS CRS selection dialog directly.
- Local CRS explanation spans the full width beneath the two CRS columns.
- GeoJSON and KML are disabled in Local CRS mode.
