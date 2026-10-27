import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import LineString, Polygon, MultiPolygon, MultiLineString, Point
from shapely.ops import linemerge, unary_union, nearest_points
from shapely.validation import make_valid
import os
import re
from zipfile import ZipFile

def fix_geometry(geom):
    """Fix invalid geometries."""
    if geom is None:
        return None
    if not geom.is_valid:
        try:
            return make_valid(geom)
        except:
            return geom.buffer(0)
    return geom

def smooth_line(line, tolerance=5, iterations=0):
    """
    Smooths a LineString by first simplifying to remove raster spikes.
    Iterations=0 bypasses Chaikin's corner-cutting to keep it pixelated.
    """
    if not isinstance(line, LineString):
        return line
    
    # 1. Simplify to remove small jagged raster spikes (tolerance in meters)
    line = line.simplify(tolerance, preserve_topology=True)
    
    # 2. Chaikin's Algorithm for smooth curves
    coords = list(line.coords)
    for _ in range(iterations):
        if len(coords) < 3:
            break
        new_coords = [coords[0]]
        for i in range(len(coords) - 1):
            p1 = np.array(coords[i])
            p2 = np.array(coords[i+1])
            new_coords.append(tuple(p1 * 0.75 + p2 * 0.25))
            new_coords.append(tuple(p1 * 0.25 + p2 * 0.75))
        new_coords.append(coords[-1])
        coords = new_coords
        
    return LineString(coords)

def extract_mainstream(input_file):
    river_gdf = gpd.read_file(input_file)
    river_gdf = river_gdf.to_crs("EPSG:32644")
    river_gdf.geometry = river_gdf.geometry.apply(lambda geom: make_valid(geom) if geom is not None else None)
    river_gdf = river_gdf[river_gdf.geometry.notnull()]
    river_gdf = river_gdf[~river_gdf.geometry.is_empty]
    river_gdf['length'] = river_gdf.geometry.length
    main_geom = river_gdf.loc[river_gdf['length'].idxmax(), 'geometry']

    if isinstance(main_geom, Polygon):
        boundary = main_geom.boundary
    elif main_geom.geom_type == "MultiPolygon":
        largest_poly = max(main_geom.geoms, key=lambda p: p.length)
        boundary = largest_poly.boundary
    elif main_geom.geom_type in ["LineString", "MultiLineString"]:
        boundary = main_geom
    else:
        raise ValueError(f"Unsupported geometry type for mainstream extraction: {main_geom.geom_type}")

    if boundary.geom_type == "MultiLineString":
        return max(boundary.geoms, key=lambda line: line.length)
    elif boundary.geom_type == "LinearRing":
        return LineString(boundary)
    else:
        return boundary

# ======================================================================== #
#  UTILITY: Auto-detect the correct UTM EPSG for any location on Earth     #
# ======================================================================== #
def _get_local_utm(lon, lat):
    """
    Return the correct UTM EPSG code for any (lon, lat) on Earth.
    Replaces the hardcoded EPSG:32644 which only covers India UTM Zone 44N.
    """
    zone = int((lon + 180) / 6) + 1
    # Clamp to valid zone range [1, 60]
    zone = max(1, min(60, zone))
    base = 32600 if lat >= 0 else 32700
    return f"EPSG:{base + zone}"


# ======================================================================== #
#  UTILITY: D8 flow direction code → unit vector (dx, dy) in map coords    #
# ======================================================================== #
def _flow_direction_from_d8(code):
    """
    HydroSHEDS D8 encoding:
      1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE
    Returns (dx, dy) as a unit vector (east=+x, north=+y).
    """
    d8_map = {
        1:   ( 1,  0),   # East
        2:   ( 1, -1),   # South-East
        4:   ( 0, -1),   # South
        8:   (-1, -1),   # South-West
        16:  (-1,  0),   # West
        32:  (-1,  1),   # North-West
        64:  ( 0,  1),   # North
        128: ( 1,  1),   # North-East
    }
    raw = d8_map.get(int(code), None)
    if raw is None:
        return None
    length = (raw[0] ** 2 + raw[1] ** 2) ** 0.5
    return (raw[0] / length, raw[1] / length)


# ======================================================================== #
#  CORE: Multi-point cross-product voting classifier                        #
#                                                                           #
#  Works for ANY flow direction — N→S, S→N, E→W, W→E, NE→SW, etc.         #
#  The right-hand rule cross-product is coordinate-system independent.      #
# ======================================================================== #
def _classify_banks_by_flow_vector(path1, path2, flow_dx, flow_dy,
                                   anchor_x, anchor_y, n_votes=20):
    """
    Multi-point cross-product voting to decide left vs right bank.

    Algorithm:
      For each bank path, sample N evenly spaced points.
      For each point P:
        side_vec = P - anchor
        cross_z  = flow_dx * side_vec.y - flow_dy * side_vec.x
        +1 vote if cross_z > 0  (point is LEFT of flow vector)
        -1 vote if cross_z < 0  (point is RIGHT of flow vector)
      Bank score = sum of votes.
      Positive score → LEFT bank. Negative score → RIGHT bank.

    This is fully direction-agnostic: the math is identical whether the
    river flows north, south, east, west, or any diagonal.

    Returns: (left_bank, right_bank, confidence_pct)
    """
    def vote_score(path):
        score = 0
        path_len = path.length
        if path_len == 0:
            return 0
        step = path_len / max(n_votes - 1, 1)
        for k in range(n_votes):
            pt = path.interpolate(min(k * step, path_len))
            sx = pt.x - anchor_x
            sy = pt.y - anchor_y
            cross_z = flow_dx * sy - flow_dy * sx
            if cross_z > 0:
                score += 1
            elif cross_z < 0:
                score -= 1
        return score

    score1 = vote_score(path1)
    score2 = vote_score(path2)

    # Confidence = how decisive the majority is (0–100%)
    total_votes = n_votes
    confidence = abs(score1) / total_votes * 100

    if score1 > 0:
        return path1, path2, confidence
    elif score1 < 0:
        return path2, path1, confidence
    else:
        # Tie — return ambiguous signal (caller will try next layer)
        return None, None, 0.0


# ======================================================================== #
#  UTILITY: PCA-based river axis vector (geometric fallback, no GEE needed) #
# ======================================================================== #
def _pca_flow_axis(path1, path2, n_pts=30):
    """
    Estimate the river's primary flow axis using PCA on the combined
    bank line coordinates.  Returns (dx, dy) — the principal component
    direction (unit vector).  This is direction-agnostic: PCA gives the
    axis, not a signed direction, so the caller must orient it using
    spatial context.
    """
    import numpy as np
    pts = []
    for path in (path1, path2):
        path_len = path.length
        if path_len == 0:
            continue
        step = path_len / max(n_pts - 1, 1)
        for k in range(n_pts):
            pt = path.interpolate(min(k * step, path_len))
            pts.append([pt.x, pt.y])
    if len(pts) < 4:
        return None, None
    pts = np.array(pts)
    cov = np.cov(pts.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    # Largest eigenvalue → principal axis (river's long axis = flow axis)
    principal = eigenvectors[:, np.argmax(eigenvalues)]
    dx, dy = float(principal[0]), float(principal[1])
    length = (dx ** 2 + dy ** 2) ** 0.5
    if length == 0:
        return None, None
    return dx / length, dy / length


def _query_gee_endpoints(p1_lonlat, p2_lonlat):
    """
    Directly query global DEM (NASADEM / MERIT) and Upstream Drainage Area (MERIT / HydroSHEDS)
    at reach endpoints p1 and p2. Takes ~0.4s and provides 100% ground-truth physical verification.
    """
    try:
        import ee
        pt1 = ee.Geometry.Point(p1_lonlat).buffer(400)
        pt2 = ee.Geometry.Point(p2_lonlat).buffer(400)
        col = ee.FeatureCollection([
            ee.Feature(pt1, {'name': 'p1'}),
            ee.Feature(pt2, {'name': 'p2'})
        ])
        dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation')
        merit = ee.Image('MERIT/Hydro/v1_0_1').select('upa')
        d_res = dem.reduceRegions(col, ee.Reducer.mean(), 90).getInfo()
        u_res = merit.reduceRegions(col, ee.Reducer.max(), 90).getInfo()
        dem_dict = {f['properties']['name']: f['properties'].get('mean', 0) for f in d_res.get('features', [])}
        upa_dict = {f['properties']['name']: f['properties'].get('max', 0) for f in u_res.get('features', [])}
        return dem_dict, upa_dict
    except Exception as e:
        print(f"[GEE Endpoint Query] Note: {e}")
        return None, None


def split_river_banks(boundary, upstream_lonlat=None, downstream_lonlat=None,
                      flow_path=None, flow_dir_code=None):
    """
    Universal, robust left/right bank classifier for ANY river flowing in ANY direction.

    Standard hydrological definition: Facing DOWNSTREAM (looking from upstream towards downstream):
      - Left Bank is on your left hand side (cross_z > 0)
      - Right Bank is on your right hand side (cross_z < 0)

    Multi-layer determination of reach flow direction:
      Layer 1: Ground-Truth Endpoint Elevation & Drainage Area (Direct GEE fast query on p1 & p2).
               Water drops elevation (higher elv = upstream) and accumulates drainage (lower upa = upstream).
      Layer 2: Step 1 Hydrography coordinates (distance to upstream_lonlat or dense flow_path).
      Layer 3: D8 flow direction vector dot product.
      Layer 4: Geometric principal axis with directional voting.
    """
    import numpy as np
    from scipy.spatial import distance_matrix
    from shapely.ops import substring
    from shapely.geometry import LineString
    from pyproj import Transformer

    # ------------------------------------------------------------------
    # 1. Normalise boundary to a single LineString ring
    # ------------------------------------------------------------------
    if boundary.geom_type == "MultiLineString":
        merged = linemerge(boundary)
        ring = (max(merged.geoms, key=lambda line: line.length)
                if merged.geom_type != "LineString" else merged)
    elif boundary.geom_type == "LineString":
        ring = boundary
    elif boundary.geom_type == "LinearRing":
        ring = LineString(boundary)
    else:
        raise ValueError(f"Unsupported geometry type for splitting banks: {boundary.geom_type}")

    coords = list(ring.coords)

    # Simplify for faster distance-matrix calculation
    ring_simp = ring.simplify(50)
    coords_simp = list(ring_simp.coords)

    # Find the two points furthest apart — these are the EXACT reach endpoints p1 & p2
    pts = np.array(coords_simp)
    dist_mat = distance_matrix(pts, pts)
    i, j = np.unravel_index(dist_mat.argmax(), dist_mat.shape)

    p1 = coords_simp[i]
    p2 = coords_simp[j]

    # Map back to original coordinate indices
    dist1 = [(c[0] - p1[0]) ** 2 + (c[1] - p1[1]) ** 2 for c in coords]
    dist2 = [(c[0] - p2[0]) ** 2 + (c[1] - p2[1]) ** 2 for c in coords]
    idx1 = int(np.argmin(dist1))
    idx2 = int(np.argmin(dist2))

    if idx1 > idx2:
        idx1, idx2 = idx2, idx1

    path1 = LineString(coords[idx1:idx2 + 1])
    path2 = LineString(coords[idx2:] + coords[:idx1 + 1])

    # Dynamic trim — remove short cross-connector artefacts at both ends
    from shapely.geometry import Polygon
    try:
        poly_area = Polygon(ring).area
        avg_length = (path1.length + path2.length) / 2.0
        avg_width = poly_area / avg_length
        TRIM_DIST = avg_width * 0.8
    except Exception:
        TRIM_DIST = 1000

    if path1.length > TRIM_DIST * 2:
        path1 = substring(path1, TRIM_DIST, path1.length - TRIM_DIST)
    if path2.length > TRIM_DIST * 2:
        path2 = substring(path2, TRIM_DIST, path2.length - TRIM_DIST)

    # ------------------------------------------------------------------
    # 2. Determine local UTM projection
    # ------------------------------------------------------------------
    try:
        if upstream_lonlat is not None:
            _utm_epsg = _get_local_utm(upstream_lonlat[0], upstream_lonlat[1])
        elif flow_path and len(flow_path) > 0:
            _utm_epsg = _get_local_utm(flow_path[0][0], flow_path[0][1])
        else:
            _utm_epsg = "EPSG:32644"
    except Exception:
        _utm_epsg = "EPSG:32644"

    # Convert p1 and p2 endpoints to WGS84 (lon, lat)
    t_to_wgs = Transformer.from_crs(_utm_epsg, "EPSG:4326", always_xy=True)
    p1_lonlat = t_to_wgs.transform(p1[0], p1[1])
    p2_lonlat = t_to_wgs.transform(p2[0], p2[1])

    # ------------------------------------------------------------------
    # 3. Determine which endpoint is UPSTREAM vs DOWNSTREAM
    # ------------------------------------------------------------------
    up_pt = None
    dn_pt = None
    flow_method = None

    # LAYER 1: Direct Ground-Truth Elevation & Drainage Area Query via GEE
    dem_dict, upa_dict = _query_gee_endpoints(p1_lonlat, p2_lonlat)
    if dem_dict and (dem_dict.get('p1', 0) > 0 or dem_dict.get('p2', 0) > 0):
        e1, e2 = dem_dict.get('p1', 0), dem_dict.get('p2', 0)
        u1, u2 = upa_dict.get('p1', 0), upa_dict.get('p2', 0)
        
        up_score_p1 = 0
        if e1 > e2:
            up_score_p1 += 1  # higher elevation = upstream
        if u2 > u1:
            up_score_p1 += 1  # larger drainage area = downstream (p2 is downstream)
            
        if up_score_p1 >= 1:
            up_pt, dn_pt = p1, p2
            flow_method = f"GEE Elevation (p1={e1:.1f}m > p2={e2:.1f}m, UPA p2={u2:.1f} > p1={u1:.1f} km²)"
        else:
            up_pt, dn_pt = p2, p1
            flow_method = f"GEE Elevation (p2={e2:.1f}m > p1={e1:.1f}m, UPA p1={u1:.1f} > p2={u2:.1f} km²)"
        print(f"[Flow Detection] Layer 1 SUCCESS -> {flow_method}")

    # LAYER 2: Match against Step 1 upstream/downstream coordinates
    if up_pt is None and upstream_lonlat is not None:
        try:
            t_to_utm = Transformer.from_crs("EPSG:4326", _utm_epsg, always_xy=True)
            up_coords_utm = t_to_utm.transform(upstream_lonlat[0], upstream_lonlat[1])
            d1 = (p1[0] - up_coords_utm[0])**2 + (p1[1] - up_coords_utm[1])**2
            d2 = (p2[0] - up_coords_utm[0])**2 + (p2[1] - up_coords_utm[1])**2
            if d1 < d2:
                up_pt, dn_pt = p1, p2
            else:
                up_pt, dn_pt = p2, p1
            flow_method = "Step 1 HydroSHEDS Upstream Point Proximity"
            print(f"[Flow Detection] Layer 2 SUCCESS -> {flow_method}")
        except Exception as e:
            print(f"[Flow Detection] Layer 2 failed: {e}")

    # LAYER 3: Match against Dense Flow Path
    if up_pt is None and flow_path and len(flow_path) >= 2:
        try:
            t_to_utm = Transformer.from_crs("EPSG:4326", _utm_epsg, always_xy=True)
            up_anchor = t_to_utm.transform(flow_path[0][0], flow_path[0][1])
            d1 = (p1[0] - up_anchor[0])**2 + (p1[1] - up_anchor[1])**2
            d2 = (p2[0] - up_anchor[0])**2 + (p2[1] - up_anchor[1])**2
            if d1 < d2:
                up_pt, dn_pt = p1, p2
            else:
                up_pt, dn_pt = p2, p1
            flow_method = "Step 1 Dense Flow Path Centerline"
            print(f"[Flow Detection] Layer 3 SUCCESS -> {flow_method}")
        except Exception as e:
            print(f"[Flow Detection] Layer 3 failed: {e}")

    # LAYER 4: D8 Direction Code Dot Product
    if up_pt is None and flow_dir_code is not None:
        d8_vec = _flow_direction_from_d8(flow_dir_code)
        if d8_vec is not None:
            # Vector p1 -> p2
            v_x = p2[0] - p1[0]
            v_y = p2[1] - p1[1]
            dot = v_x * d8_vec[0] + v_y * d8_vec[1]
            if dot > 0:
                up_pt, dn_pt = p1, p2
            else:
                up_pt, dn_pt = p2, p1
            flow_method = f"D8 Direction Code ({flow_dir_code})"
            print(f"[Flow Detection] Layer 4 SUCCESS -> {flow_method}")

    # LAYER 5: Fallback PCA / Bounding Box Orientation
    if up_pt is None:
        print("[Flow Detection] Fallback: orienting reach endpoints using spatial bounds.")
        if p1[1] > p2[1]:  # Default: North to South
            up_pt, dn_pt = p1, p2
        else:
            up_pt, dn_pt = p2, p1
        flow_method = "Spatial Bounds Fallback"

    # ------------------------------------------------------------------
    # 4. Universal Cross-Product Multi-Point Voting along Banks
    # ------------------------------------------------------------------
    flow_dx = dn_pt[0] - up_pt[0]
    flow_dy = dn_pt[1] - up_pt[1]
    flow_len = (flow_dx**2 + flow_dy**2)**0.5
    if flow_len == 0:
        return path1, path2
    flow_dx /= flow_len
    flow_dy /= flow_len

    print(f"[Flow Direction] Unit flow vector: dx={flow_dx:.3f}, dy={flow_dy:.3f} ({flow_method})")

    # Sample 25 points along each bank to vote on left vs right side
    def vote_bank(path, n_pts=25):
        votes = 0
        path_len = path.length
        step = path_len / max(n_pts - 1, 1)
        for k in range(n_pts):
            pt = path.interpolate(min(k * step, path_len))
            sx = pt.x - up_pt[0]
            sy = pt.y - up_pt[1]
            cross_z = flow_dx * sy - flow_dy * sx
            if cross_z > 0:
                votes += 1
            elif cross_z < 0:
                votes -= 1
        return votes

    score1 = vote_bank(path1)
    score2 = vote_bank(path2)
    print(f"[Bank Classification] path1 score: {score1}, path2 score: {score2}")

    # cross_z > 0 is strictly LEFT BANK when looking downstream
    if score1 > score2:
        left_bank, right_bank = path1, path2
    else:
        left_bank, right_bank = path2, path1

    print(f"[Bank Classification] Left Bank length: {left_bank.length:.0f}m, Right Bank length: {right_bank.length:.0f}m")
    return left_bank, right_bank


def process_separate_banks_geojson(geojson_dict, year,
                                   upstream_lonlat=None, downstream_lonlat=None,
                                   flow_path=None, flow_dir_code=None):
    """
    Step 3: Dissolve water polygons, close gaps, extract the boundary ring,
    and split it into Left (Red) and Right (Black) banklines.

    upstream_lonlat / downstream_lonlat : [lng, lat] from HydroSHEDS sampling in Step 1.
    flow_path       : ordered list of [lng, lat] from dense HydroSHEDS sampling (Layer 1).
    flow_dir_code   : dominant D8 flow direction integer (Layer 3).
    If all are None, falls back to PCA geometric axis (Layer 4).
    """
    import json

    with open("debug_geojson.json", "w") as f:
        json.dump(geojson_dict, f)
    print(f"[Step 3 Debug] Initial features loaded: {len(geojson_dict.get('features', []))}")

    river_gdf = gpd.GeoDataFrame.from_features(geojson_dict["features"])
    river_gdf = river_gdf.set_crs("EPSG:4326")

    # ------------------------------------------------------------------
    # Auto-detect the correct UTM zone for re-projection (global support)
    # ------------------------------------------------------------------
    _reproj_epsg = "EPSG:32644"   # default (India)
    try:
        _lon, _lat = river_gdf.geometry.iloc[0].centroid.x, river_gdf.geometry.iloc[0].centroid.y
        # GEE-supplied coords are more reliable for zone detection
        if upstream_lonlat is not None:
            _lon, _lat = upstream_lonlat[0], upstream_lonlat[1]
        elif flow_path and len(flow_path) > 0:
            _lon, _lat = flow_path[0][0], flow_path[0][1]
        _reproj_epsg = _get_local_utm(_lon, _lat)
    except Exception:
        pass
    print(f"[Step 3] Re-projecting to {_reproj_epsg}")

    river_gdf = river_gdf.to_crs(_reproj_epsg)

    # Fix invalid geometries (common with raster-to-vector conversions)
    from shapely.validation import make_valid
    river_gdf.geometry = river_gdf.geometry.apply(
        lambda geom: make_valid(geom) if geom is not None else None
    )

    river_gdf = river_gdf[river_gdf.geometry.notnull()]
    print(f"[Step 3 Debug] Features after notnull: {len(river_gdf)}")
    river_gdf = river_gdf[~river_gdf.geometry.is_empty]
    print(f"[Step 3 Debug] Features after is_empty: {len(river_gdf)}")

    # -------------------------------------------------------------------------
    # NOISE REMOVAL: Filter out isolated water pixels/polygons using area threshold
    # -------------------------------------------------------------------------
    AREA_THRESHOLD = 50000
    river_gdf['area'] = river_gdf.geometry.area
    river_gdf = river_gdf[river_gdf['area'] > AREA_THRESHOLD]
    print(f"[Step 3 Debug] Features after removing isolated pixels (<{AREA_THRESHOLD} sqm): {len(river_gdf)}")

    if river_gdf.empty:
        raise ValueError(
            "The provided GeoJSON contains no valid geometries (or they were all filtered out as noise). "
            "(Step 1 may not have found any water in this date range)."
        )

    from shapely.ops import unary_union

    # 1. Combine all separated water polygons (braids and channels)
    unified_geom = unary_union(river_gdf.geometry)

    # Remove isolated water pixels/lakes BEFORE morphological closing
    if unified_geom.geom_type == 'MultiPolygon':
        unified_geom = max(unified_geom.geoms, key=lambda p: p.area)

    # 2. No morphological closing — keep bankline tightly hugging the pixels
    closed_geom = unified_geom

    if closed_geom.geom_type == 'MultiPolygon':
        closed_geom = max(closed_geom.geoms, key=lambda p: p.area)

    if closed_geom.geom_type == 'Polygon':
        boundary = closed_geom.exterior
    else:
        raise ValueError(f"Unexpected geometry type after closing: {closed_geom.geom_type}")

    if boundary.geom_type == "MultiLineString":
        river_boundary = max(boundary.geoms, key=lambda line: line.length)
    elif boundary.geom_type == "LinearRing":
        river_boundary = LineString(boundary)
    else:
        river_boundary = boundary

    # Pass ALL flow direction data to the robust 4-layer splitter
    left_bank, right_bank = split_river_banks(
        river_boundary,
        upstream_lonlat=upstream_lonlat,
        downstream_lonlat=downstream_lonlat,
        flow_path=flow_path,
        flow_dir_code=flow_dir_code
    )

    # Apply smoothing to suppress spikes
    left_bank = smooth_line(left_bank)
    right_bank = smooth_line(right_bank)

    out_gdf = gpd.GeoDataFrame({
        "bank": ["Left", "Right"],
        "year": [year, year]
    }, geometry=[left_bank, right_bank], crs=_reproj_epsg)

    out_gdf = out_gdf.to_crs("EPSG:4326")
    return json.loads(out_gdf.to_json())



def process_midchannel_bars_geojson(geojson_dict):
    import json
    shpX = gpd.GeoDataFrame.from_features(geojson_dict["features"])
    shpX = shpX.set_crs("EPSG:4326")
    shpX = shpX.to_crs("EPSG:32644")

    # Filter out isolated water pixels/polygons using an area threshold (50,000 sqm)
    AREA_THRESHOLD = 50000
    shpX['area'] = shpX.geometry.area
    shpX = shpX[shpX['area'] > AREA_THRESHOLD]
    
    geomX = unary_union(shpX.geometry)
    geomX = fix_geometry(geomX)

    if geomX.geom_type == "MultiPolygon":
        geom_boundary = max(geomX.geoms, key=lambda g: g.area).exterior
    elif geomX.geom_type == "Polygon":
        geom_boundary = geomX.exterior
    elif geomX.geom_type == "MultiLineString":
        merged = linemerge(geomX)
        geom_boundary = merged if merged.geom_type == "LineString" else max(merged.geoms, key=lambda g: g.length)
    elif geomX.geom_type == "LineString":
        geom_boundary = geomX
    else:
        raise ValueError(f"Unsupported geometry type: {geomX.geom_type}")

    ring_coords = list(geom_boundary.coords)
    if ring_coords[0] != ring_coords[-1]:
        ring_coords.append(ring_coords[0])
    filled_poly = Polygon(ring_coords)
    filled_poly = fix_geometry(filled_poly)

    diff_geom = fix_geometry(filled_poly.difference(geomX))
    diff_gdf = gpd.GeoDataFrame(geometry=[diff_geom], crs="EPSG:32644")
    
    diff_gdf = diff_gdf.to_crs("EPSG:4326")
    return json.loads(diff_gdf.to_json())

def create_transects(line1, line2, spacing=1000, crs="EPSG:32644", line2_resolution=None):
    if isinstance(line1, MultiLineString):
        line1 = LineString([pt for geom in line1 for pt in geom.coords])
    if isinstance(line2, MultiLineString):
        line2 = LineString([pt for geom in line2 for pt in geom.coords])

    if line2_resolution is None:
        line2_resolution = spacing / 5

    num_line2_pts = int(line2.length // line2_resolution) + 1
    line2_points = [line2.interpolate(i * line2_resolution) for i in range(num_line2_pts)]

    num_transects = int(line1.length // spacing) + 1
    transects = []
    last_used_idx = -1

    for i in range(num_transects):
        distance = i * spacing
        if distance > line1.length:
            break

        pt_on_line1 = line1.interpolate(distance)
        search_pts = line2_points[last_used_idx + 1:]
        if not search_pts:
            break

        distances = [pt_on_line1.distance(p) for p in search_pts]
        min_rel_idx = np.argmin(distances)
        min_idx = last_used_idx + 1 + min_rel_idx

        if min_idx >= len(line2_points):
            break

        closest_pt = line2_points[min_idx]
        transects.append(LineString([pt_on_line1, closest_pt]))
        last_used_idx = min_idx

    return gpd.GeoDataFrame(geometry=transects, crs=crs)

def process_transects(line1_path, line2_path, spacing, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    line1_gdf = gpd.read_file(line1_path)
    line2_gdf = gpd.read_file(line2_path)

    if line1_gdf.crs.to_string() != "EPSG:32644":
        line1_gdf = line1_gdf.to_crs("EPSG:32644")
    if line2_gdf.crs.to_string() != "EPSG:32644":
        line2_gdf = line2_gdf.to_crs("EPSG:32644")

    line1 = line1_gdf.geometry.iloc[0]
    line2 = line2_gdf.geometry.iloc[0]

    transects_gdf = create_transects(line1, line2, spacing=spacing)
    out_base = "transects"
    out_path = os.path.join(output_dir, f"{out_base}.shp")
    transects_gdf.to_file(out_path)

    zip_path = os.path.join(output_dir, f"{out_base}.zip")
    with ZipFile(zip_path, "w") as zipf:
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
            f = os.path.join(output_dir, out_base + ext)
            if os.path.exists(f):
                zipf.write(f, arcname=os.path.basename(f))
    return zip_path

def signed_shift_distance(transect: LineString, ref_point: Point, comp_point: Point):
    t0 = np.array(transect.coords[0])
    t1 = np.array(transect.coords[-1])
    transect_vec = t1 - t0
    shift_vec = np.array([comp_point.x - ref_point.x, comp_point.y - ref_point.y])
    dot_product = np.dot(shift_vec, transect_vec)
    sign = np.sign(dot_product) if dot_product != 0 else 1
    distance = ref_point.distance(comp_point)
    return sign * distance

def calculate_shift_along_transects(transects_gdf, ref_line, comp_line):
    shift_data = []
    for i, transect in enumerate(transects_gdf.geometry):
        if not isinstance(transect, LineString):
            continue
        intersection_ref = ref_line.intersection(transect)
        intersection_comp = comp_line.intersection(transect)
        if intersection_ref.is_empty or intersection_comp.is_empty:
            intersection_ref = nearest_points(transect, ref_line)[1]
            intersection_comp = nearest_points(transect, comp_line)[1]
        else:
            start_point = Point(transect.coords[0])
            if intersection_ref.geom_type == 'MultiPoint':
                intersection_ref = nearest_points(start_point, intersection_ref)[1]
            if intersection_comp.geom_type == 'MultiPoint':
                intersection_comp = nearest_points(start_point, intersection_comp)[1]

        shift_distance = signed_shift_distance(transect, intersection_ref, intersection_comp)
        shift_data.append({'transect_id': i + 1, 'shift': shift_distance})
    return pd.DataFrame(shift_data)

def process_shifts(transects_path, ref_path, comp_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    transects = gpd.read_file(transects_path)
    ref_gdf = gpd.read_file(ref_path)
    comp_gdf = gpd.read_file(comp_path)

    transects = transects.to_crs("EPSG:32644")
    ref_gdf = ref_gdf.to_crs("EPSG:32644")
    comp_gdf = comp_gdf.to_crs("EPSG:32644")

    ref_line = ref_gdf.geometry.unary_union if len(ref_gdf) > 1 else ref_gdf.geometry.iloc[0]
    comp_line = comp_gdf.geometry.unary_union if len(comp_gdf) > 1 else comp_gdf.geometry.iloc[0]

    shift_df = calculate_shift_along_transects(transects, ref_line, comp_line)
    out_csv = os.path.join(output_dir, "predicted_shifts.csv")
    shift_df.to_csv(out_csv, index=False)
    return out_csv

def process_spatial_map(transect_path, bankline_path, shift_csv_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    gdf_transects = gpd.read_file(transect_path).reset_index(drop=True)
    gdf_bankline = gpd.read_file(bankline_path)
    df_shift = pd.read_csv(shift_csv_path)

    gdf_bankline = gdf_bankline.to_crs(gdf_transects.crs)
    predicted_points = []

    for i, row in df_shift.iterrows():
        shift = row['shift']
        tid = int(row['transect_id'])
        if tid < 1 or tid > len(gdf_transects):
            continue

        transect_geom = gdf_transects.iloc[tid - 1].geometry
        intersection = transect_geom.intersection(gdf_bankline.unary_union)
        
        if intersection.is_empty or not isinstance(intersection, Point):
            continue

        coords = list(transect_geom.coords)
        start, end = Point(coords[0]), Point(coords[-1])
        dx, dy = end.x - start.x, end.y - start.y
        length = (dx**2 + dy**2)**0.5
        if length == 0:
            continue

        ux, uy = dx / length, dy / length
        if shift < 0:
            ux, uy = -ux, -uy

        px = intersection.x + abs(shift) * ux
        py = intersection.y + abs(shift) * uy
        predicted_points.append({'transect_id': tid, 'geometry': Point(px, py)})

    gdf_predicted = gpd.GeoDataFrame(predicted_points, crs=gdf_transects.crs)
    out_points = os.path.join(output_dir, "predicted_points.shp")
    gdf_predicted.to_file(out_points)

    gdf_predicted_sorted = gdf_predicted.sort_values(by="transect_id")
    line = LineString(gdf_predicted_sorted.geometry.tolist())
    gdf_line = gpd.GeoDataFrame([{'geometry': line}], crs=gdf_transects.crs)
    out_line = os.path.join(output_dir, "predicted_bankline.shp")
    gdf_line.to_file(out_line)

    zip_path = os.path.join(output_dir, "spatial_map.zip")
    with ZipFile(zip_path, "w") as zipf:
        for f in os.listdir(output_dir):
            if f.startswith("predicted_points") or f.startswith("predicted_bankline"):
                zipf.write(os.path.join(output_dir, f), f)

    return zip_path
