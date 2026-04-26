"""Parse city boundary files into a Shapely polygon."""
import json
import xml.etree.ElementTree as ET
import zipfile
import io
import csv
from pathlib import Path
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union
import fiona


def _ensure_valid(geom):
    """Repair invalid polygons (self-intersections, etc.) so OSMnx accepts them."""
    if geom.is_valid:
        return geom
    try:
        from shapely.validation import make_valid
        fixed = make_valid(geom)
    except Exception:
        fixed = geom.buffer(0)
    if fixed.geom_type == "GeometryCollection":
        polys = [g for g in fixed.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if polys:
            fixed = unary_union(polys)
    return fixed


def parse_geojson(data: bytes) -> Polygon | MultiPolygon:
    fc = json.loads(data)
    geoms = []
    if fc.get("type") == "FeatureCollection":
        for f in fc["features"]:
            geoms.append(shape(f["geometry"]))
    elif fc.get("type") == "Feature":
        geoms.append(shape(fc["geometry"]))
    else:
        geoms.append(shape(fc))
    result = unary_union(geoms)
    return _ensure_valid(result)


def parse_kml(data: bytes) -> Polygon | MultiPolygon:
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(data)
    coords_list = []
    for coord_el in root.iter("{http://www.opengis.net/kml/2.2}coordinates"):
        text = coord_el.text.strip()
        coords = []
        for triplet in text.split():
            parts = triplet.split(",")
            lon, lat = float(parts[0]), float(parts[1])
            coords.append((lon, lat))
        if coords:
            coords_list.append(coords)

    geoms = []
    for ring in coords_list:
        if len(ring) >= 3:
            geoms.append(Polygon(ring))
    return _ensure_valid(unary_union(geoms))


def _read_shapefile_as_wgs84(shp_path: Path) -> Polygon | MultiPolygon:
    """Read a shapefile and reproject to WGS84 lon/lat if needed."""
    import geopandas as gpd
    gdf = gpd.read_file(str(shp_path))
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    geoms = list(gdf.geometry.values)
    return _ensure_valid(unary_union(geoms))


def parse_shapefile(data: bytes) -> Polygon | MultiPolygon:
    # Expect a zip containing .shp, .dbf, .shx etc.
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shp_name = next(n for n in zf.namelist() if n.endswith(".shp"))
        tmp_dir = Path("/tmp/city_boundary_shp")
        tmp_dir.mkdir(exist_ok=True)
        zf.extractall(tmp_dir)
    return _read_shapefile_as_wgs84(tmp_dir / shp_name)


def parse_shapefile_dir(directory: Path) -> Polygon | MultiPolygon:
    shp_files = list(directory.glob("*.shp"))
    if not shp_files:
        raise ValueError("No .shp file found in directory")
    return _read_shapefile_as_wgs84(shp_files[0])


def parse_csv_boundary(data: bytes) -> Polygon | MultiPolygon:
    # CSV format: rows with lat,lon columns defining boundary polygon
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    coords = []
    for row in reader:
        keys = [k.lower() for k in row.keys()]
        if "latitude" in keys and "longitude" in keys:
            lat = float(row[list(row.keys())[keys.index("latitude")]])
            lon = float(row[list(row.keys())[keys.index("longitude")]])
            coords.append((lon, lat))
        elif "lat" in keys and "lon" in keys:
            lat = float(row[list(row.keys())[keys.index("lat")]])
            lon = float(row[list(row.keys())[keys.index("lon")]])
            coords.append((lon, lat))
    if not coords:
        raise ValueError("CSV must have lat/lon or latitude/longitude columns")
    return _ensure_valid(Polygon(coords))


def polygon_to_geojson(poly: Polygon | MultiPolygon) -> dict:
    from shapely.geometry import mapping
    return {"type": "Feature", "geometry": mapping(poly), "properties": {}}
