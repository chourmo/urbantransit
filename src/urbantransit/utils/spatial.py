### Spatial utility functions

import math

import geopandas as gpd
import listandstruct as ls
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import shapely as sh

EARTH_RADIUS = 6378137.0  # in meters
EARTH_FLATTENING = 1 / 298.257223563  # WGS-84 flattening factor

# ------------------------------------------------------------------


def _is_list_series(df: pd.Series) -> bool:
    """Test is df is a Series of ListArrays"""
    if isinstance(df, pd.Series) and isinstance(df.dtype, pd.ArrowDtype):
        return isinstance(df.dtype.pyarrow_dtype, pa.ListType)
    else:
        return False


def Points(x, y, crs):
    """create a GeoSeries from points coordinates"""
    return gpd.GeoSeries(gpd.points_from_xy(x, y), index=x.index, crs=crs)


def Linestrings(x, y, by=None, crs=None):
    """create a linestring from coordinates"""

    indices = None
    if _is_list_series(x):
        indices = pa.array(x).value_parent_indices()
        x = pa.array(x).flatten()
    if _is_list_series(y):
        if indices is None:
            indices = pa.array(y).value_parent_indices()
        y = pa.array(y).flatten()

    if indices is None:
        indices = pd.factorize(by)[0]

    if by is None:
        geoms = sh.linestrings(coords=x, y=y, indices=indices)
        return gpd.GeoSeries(geoms, crs=crs)

    gdf = by.drop_duplicates()
    geoms = sh.linestrings(coords=x, y=y, indices=indices)
    return gpd.GeoDataFrame(gdf, geometry=geoms, crs=crs)


def connect_points(pt1, pt2):
    if not isinstance(pt1, gpd.GeoSeries):
        raise TypeError("pt1 must be a Geoseries")
    if not isinstance(pt2, gpd.GeoSeries):
        raise TypeError("pt2 must be a Geoseries")

    point1 = pt1.get_coordinates()
    point2 = pt2.get_coordinates()
    point1.index.name = "_idx"
    point2.index.name = "_idx"

    coords = pd.concat([point1, point2], keys=[0, 1]).sort_index(level=[1, 0])
    coords = coords.reset_index(level=1)

    geoms = Linestrings(x=coords["x"], y=coords["y"], by=coords["_idx"], crs=pt1.crs)[
        "geometry"
    ]
    geoms.index = pt1.index
    return geoms


def degree_to_radian(angle):
    const = math.pi / 180.0
    return pc.multiply(angle, const)


def radian_to_degree(angle):
    const = 180.0 / math.pi
    return pc.multiply(angle, const)


def _bowring_inverse(lat, lon):
    """Bowring's method for geodetic to geocentric coordinates conversion"""

    e2 = 2.0 * EARTH_FLATTENING - EARTH_FLATTENING * EARTH_FLATTENING
    one_minus_e2 = 1.0 - e2

    lat_array = (
        lat
        if isinstance(lat, (pa.Array, pa.ChunkedArray))
        else pa.array(lat, from_pandas=True)
    )
    lon_array = (
        lon
        if isinstance(lon, (pa.Array, pa.ChunkedArray))
        else pa.array(lon, from_pandas=True)
    )

    lat_rad = degree_to_radian(lat_array)
    lon_rad = degree_to_radian(lon_array)

    sin_lat = pc.sin(lat_rad)
    cos_lat = pc.cos(lat_rad)
    sin_lon = pc.sin(lon_rad)
    cos_lon = pc.cos(lon_rad)

    sin_lat_sq = pc.multiply(sin_lat, sin_lat)
    denom = pc.sqrt(pc.subtract(1.0, pc.multiply(e2, sin_lat_sq)))
    N = pc.divide(EARTH_RADIUS, denom)

    N_cos_lat = pc.multiply(N, cos_lat)

    X = pc.multiply(N_cos_lat, cos_lon)
    Y = pc.multiply(N_cos_lat, sin_lon)
    Z = pc.multiply(pc.multiply(N, one_minus_e2), sin_lat)

    return X, Y, Z


def pythagore_distance(X1, Y1, Z1, X2, Y2, Z2):
    dX = pc.subtract(X2, X1)
    dY = pc.subtract(Y2, Y1)
    dZ = pc.subtract(Z2, Z1)

    distance = pc.sqrt(
        pc.add(pc.add(pc.multiply(dX, dX), pc.multiply(dY, dY)), pc.multiply(dZ, dZ))
    )
    return distance


def geographic_distance(lat1, lon1, lat2, lon2):
    """Compute distance between two points using Bowring's method"""

    X1, Y1, Z1 = _bowring_inverse(lat1, lon1)
    X2, Y2, Z2 = _bowring_inverse(lat2, lon2)

    distance = pythagore_distance(X1, Y1, Z1, X2, Y2, Z2)
    return pd.Series(distance, index=lat1.index)


def first_point(geometry):
    xy = geometry.get_coordinates()
    xy = xy.loc[~xy.index.duplicated(keep="first")]
    return Points(xy["x"], xy["y"], crs=geometry.crs)


def last_point(geometry):
    xy = geometry.get_coordinates()
    xy = xy.loc[~xy.index.duplicated(keep="last")]
    return Points(xy["x"], xy["y"], crs=geometry.crs)


def pairs(
    left,
    distance,
    right=None,
    right_distance=None,
    filter_distance="lowest",
    drop_same=None,
    prefix="to_",
    line_geometry=False,
):
    """Find pairs between origins and destinations

    Args:
        left : Points GeoDataframe of origins, use origin crs for distance calculation
        distance : int or column name in origins of distances, in origin crs units
        right : optional GeoDataFrame of destination. If None, use origin
        right_distance : optional column name in destination to use as distance
        filter_distance : optional, if max_distance and destination_distance are column name, use 'lowest' or 'highest' value
        drop_same : optional column name, drop identical values
        prefix: string added as prefix to destination column names
        line_geometry: boolean, add a linestring between pairs, with origin crs

    Returns : a GeoDataframe with from and to stops, from and to point geometries and distance columns"""

    if not isinstance(left, gpd.GeoDataFrame):
        raise TypeError("origin must be a GeoDataframe")

    if left.crs.is_geographic:
        raise TypeError(
            f"left crs {left.crs} can not be geographic for distance calculation"
        )

    if right is not None and right.crs.is_geographic:
        raise TypeError(
            f"destination crs {left.crs} can not be geographic for distance calculation"
        )

    geom = left.geometry.name
    to_geom = prefix + geom
    crs = left.crs

    # transfer origins
    df = left.copy()

    if right is None:
        df_dest = left.copy()
    elif right.crs == crs:
        df_dest = right.copy()
    else:
        df_dest = right.to_crs(crs)

    if df_dest.index.name is None:
        right_index = "index_right"
    else:
        right_index = df_dest.index.name + "_right"

    # transfer destinations
    df_dest = df_dest.rename({"from_stop": "stop"}).add_prefix(prefix, axis=1)

    # copy stop_geometry as sjoin does not keep it in results
    to_geom = prefix + geom
    df_dest = df_dest.set_geometry(to_geom, crs=crs)
    df_dest["temp_geometry"] = df_dest[to_geom].copy()
    df_dest = df_dest.set_geometry("temp_geometry")

    if right_distance is None:
        pairs = gpd.sjoin(df, df_dest, "inner", "dwithin", distance=distance)
    elif not isinstance(distance, str):
        raise ValueError(
            "If right_distance is set, distance must be a column name in left dataframe"
        )
    else:
        _maxdist = max(left[distance].max(), right[right_distance].max())
        pairs = gpd.sjoin(df, df_dest, "inner", "dwithin", distance=_maxdist)

    pairs = pairs.set_geometry(geom, crs=crs)
    pairs = pairs.reset_index(drop=True).drop(columns=right_index)
    pairs["distance"] = pairs[geom].distance(pairs[to_geom])

    if right_distance is not None and filter_distance == "lowest":
        pairs = pairs.loc[
            pairs["distance"] <= pairs[[distance, prefix + right_distance]].min(axis=1)
        ]
    elif right_distance is not None and filter_distance == "highest":
        pairs = pairs.loc[
            pairs["distance"] <= pairs[[distance, prefix + right_distance]].max(axis=1)
        ]

    if line_geometry:
        pairs["line_geometry"] = pairs[geom].shortest_line(pairs[to_geom])

    if drop_same is None:
        return pairs

    return pairs.loc[pairs[drop_same] != pairs[prefix + drop_same]].copy()


def group_boundingbox(df, groupby, xmin="xmin", ymin="ymin", xmax="xmax", ymax="ymax"):
    """Group a GeoDataFrame by groupby columns, aggregating geometries into StructArrays

    Args:
        geometry : GeoDataFrame with geometries to group
        groupby : column name or list of column names to group by
        ignore_index : boolean, if True reset index in resulting dataframe"""

    if groupby not in df.columns:
        raise ValueError(f"{groupby} not in dataframe columns")

    bbox = df[[groupby, df.geometry.name]].copy()
    bbox[[xmin, ymin, xmax, ymax]] = df.geometry.bounds.astype("float32[pyarrow]")

    bbox = bbox.groupby(groupby).agg(
        xmin=pd.NamedAgg(column=xmin, aggfunc="min"),
        xmax=pd.NamedAgg(column=xmax, aggfunc="max"),
        ymin=pd.NamedAgg(column=ymin, aggfunc="min"),
        ymax=pd.NamedAgg(column=ymax, aggfunc="max"),
    )

    return pd.Series(ls.struct_array(bbox), index=bbox.index)
