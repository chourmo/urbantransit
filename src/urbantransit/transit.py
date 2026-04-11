from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import pyarrow.parquet as pq
import pyarrow.dataset as ds
import pyarrow as pa
import geopandas as gpd
import listandstruct as ls

from sklearn.cluster import DBSCAN

from urbantransit.utils.spatial import first_point, last_point, group_boundingbox, pairs
from urbantransit.utils.ids import filter_ids
from urbantransit.utils.time import DayTime, WEEKDAYS
from urbantransit.utils.logging import transitlog
from urbantransit.utils.time import seconds_to_text, day_from_seconds
from urbantransit.graph import TransitGraph
from urbantransit.gtfs_utils import LAST_STOP


DISTANCE_TYPE = float | pd.Series | dict[str, float]


# ------------------------------------------------------------------------------------------------------
# Transit Classes


class Agencies:
    """Class representing transit agencies based on Arrow GTFS specification"""

    DATASET = "Agencies"
    FILE = "agencies.parquet"
    GID = "agency_gid"

    def __init__(self, data, year, week):

        if data.index.name != Agencies.GID:
            data = data.set_index(Agencies.GID, drop=True)

        self.data = data
        self.year = year
        self.week = week

    @classmethod
    def from_parquet_dataset(cls, path: Path, year: int, week: int) -> "Agencies":
        """Create Agencies from a dataset for year and week"""

        file_path = path / cls.DATASET

        try:
            dataset = ds.dataset(file_path, format="parquet", partitioning="hive")
            table = dataset.to_table(
                filter=((ds.field("year") == year) & (ds.field("week") == week))
            )
            table = table.to_pandas(types_mapper=pd.ArrowDtype)

            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Agencies parquet file '{path}': {e}"
            ) from e

    @classmethod
    def from_parquet(cls, path: Path, year: int, week: int) -> "Agencies":
        """Create Agencies from a path directory"""

        file_path = path / cls.FILE

        try:
            table = gpd.read_parquet(
                file_path,
                engine="pyarrow",
                to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
            )
            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Agencies parquet file '{file_path}': {e}"
            ) from e

    def to_parquet(self, path: Path):
        """Save to geoparquet file."""

        # save index if it has a name
        if self.data.index.name is not None:
            data = self.data.reset_index()
        else:
            data = self.data

        table = pa.Table.from_pandas(data, preserve_index=False)
        write_path = path / self.FILE
        pq.write_table(table, write_path)

    @staticmethod
    def pack_routes(routes: pd.DataFrame) -> pd.DataFrame:
        """Pack routes in ListArray of StructArray."""

        packed = routes.sort_values("agency_gid").copy()

        if packed.index.name == "route_gid":
            packed = packed.reset_index()

        packed["routes"] = ls.struct_array(packed.drop(columns=["agency_gid"]))
        # packed = packed.sort_values("agency_gid")[["routes", "agency_gid"]]
        packed = ls.list_array(
            packed["routes"], packed["agency_gid"], ignore_index=False
        )

        return packed.to_frame("routes")

    def add_routes(self, agencies: pd.DataFrame, routes: pd.DataFrame) -> pd.DataFrame:
        """merge agencies and routes dataframe"""

        packed = self.pack_routes(routes)

        if "routes" in agencies.columns:
            agencies = agencies.drop(columns=["routes"])

        df = pd.merge(
            agencies, packed, left_on=Agencies.GID, right_index=True, how="left"
        )

        return df

    # ----------------------------------------------------------------------

    def routes(self, agency_data: bool = False) -> pd.DataFrame:
        """Extract routes from agencies, optionnaly add agency data to each route"""
        routes = self.data["routes"].explode().struct.explode()
        if not agency_data:
            return routes.reset_index().set_index("route_gid")
        agency = self.data.drop(columns=["routes", "bbox"])
        df = routes.join(agency, how="left").reset_index()
        return df.set_index("route_gid")

    def filter_gids(self, gids) -> "Agencies":
        """Return a new Agencies object only with gids"""
        results = filter_ids(self.data, gids)
        return Agencies(results, self.year, self.week)

    def filter_routes(self, gids: pd.DataFrame) -> "Agencies":
        """Return new Agencies without routes gids"""
        routes = self.routes(agency_data=False)
        routes = routes.loc[routes.index.isin(gids.drop_duplicates().values)]

        # filter agencies to only those with routes
        agencies = self.data.loc[self.data.index.isin(routes[Agencies.GID])]
        results = self.add_routes(agencies.reset_index(), routes)
        return Agencies(results.set_index(Agencies.GID), year=self.year, week=self.week)

    def set_boundingboxes(self, points: gpd.GeoSeries):
        """Set bounding boxes for agencies based on points extracted from lines"""

        # list of unique stop points in lines with agency_id
        bbox = group_boundingbox(points, groupby=self.gid)
        self.data = self.data.merge(bbox, how="left")

        return None


class Stops:
    """Class representing transit stops based on Arrow GTFS specification"""

    DATASET = "Stops"
    FILE = "stops.parquet"
    GID = "stop_gid"

    def __init__(self, data, year, week):
        if data.index.name != Stops.GID:
            data = data.set_index(Stops.GID, drop=True)

        self.data = data
        self.year = year
        self.week = week

    @classmethod
    def from_parquet_dataset(cls, path: Path, year: int, week: int) -> "Stops":
        """Create Stops from a dataset for year and week"""

        file_path = path / cls.DATASET

        try:
            dataset = ds.dataset(file_path, format="parquet", partitioning="hive")
            table = dataset.to_table(
                filter=((ds.field("year") == year) & (ds.field("week") == week))
            )

            table = gpd.GeoDataFrame.from_arrow(
                table,
                geometry="geometry",
                to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
            )

            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Stops parquet file '{path}': {e}"
            ) from e

    @classmethod
    def from_parquet(cls, path: Path, year: int, week: int) -> "Stops":
        """Create Stops from a directory"""

        file_path = path / cls.FILE

        try:
            table = gpd.read_parquet(
                file_path,
                engine="pyarrow",
                to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
            )
            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Stops parquet file '{path}': {e}"
            ) from e

    def to_parquet(self, path: Path):
        """Save to geoparquet file."""

        # save index if it has a name
        if self.data.index.name is not None:
            data = self.data.reset_index()
        else:
            data = self.data
        data.to_parquet(path / self.FILE, index=False)
        return None

    def filter_gids(self, gids) -> "Stops":
        """Return a new Stops object only with gids"""
        results = filter_ids(self.data, gids)
        return Stops(results, self.year, self.week)


class Lines:
    """Class representing transit lines based on Arrow GTFS specification"""

    DATASET = "Lines"
    FILE = "lines.parquet"
    GID = "line_gid"

    def __init__(self, data, year, week):
        self.data = data
        self.year = year
        self.week = week

    @classmethod
    def from_parquet_dataset(cls, path: Path, year: int, week: int) -> "Lines":
        """Create Lines from a dataset for year and week."""

        file_path = path / cls.DATASET

        try:
            dataset = ds.dataset(file_path, format="parquet", partitioning="hive")
            table = dataset.to_table(
                filter=((ds.field("year") == year) & (ds.field("week") == week))
            )
            table = gpd.GeoDataFrame.from_arrow(
                table,
                geometry="geometry",
                to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
            )

            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Agencies parquet file '{path}': {e}"
            ) from e

    @classmethod
    def from_parquet(cls, path: Path, year: int, week: int) -> "Lines":
        """Create Lines from a directory."""

        file_path = path / cls.FILE

        try:
            table = gpd.read_parquet(
                file_path,
                engine="pyarrow",
                to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
            )
            return cls(table, year, week)

        except Exception as e:
            raise RuntimeError(
                f"Failed to read Agencies parquet file '{path}': {e}"
            ) from e

    def to_parquet(self, path: Path):
        """Save to geoparquet file."""

        # save index if it has a name
        if self.data.index.name is not None:
            data = self.data.reset_index()
        else:
            data = self.data

        data.to_parquet(path / self.FILE, index=False)
        return None

    def drop_on_demand(self) -> "Lines":
        data = self.data
        data = data.loc[~data.on_demand]
        return Lines(data, self.year, self.week)

    def filter_gids(self, gids) -> "Lines":
        """Return lines only with gids"""
        results = filter_ids(self.data, gids)
        return Lines(results, self.year, self.week)

    def filter_routes(self, gids) -> "Lines":
        """Return lines only with subset of route_gids"""
        results = self.data.loc[self.data["route_gid"].isin(gids)]
        return Lines(results, self.year, self.week)

    def line_stops(self, geometry: bool = False) -> gpd.GeoDataFrame:
        """Return all stop points in lines,
        time column is departure for all stops except last stop where it is arrival"""

        df = self.data.copy()

        points = df.loc[df.stop_sequence < LAST_STOP].drop(
            columns=["to_stop", "travel"]
        )
        points = points.rename(columns={"from_stop": "stop_gid", "departure": "time"})

        if geometry:
            points["geometry"] = first_point(points["geometry"])
        else:
            del points["geometry"]

        last_pts = df.loc[df.stop_sequence == LAST_STOP].drop(columns=["from_stop"])
        last_pts["time"] = last_pts["departure"].listarray.add(last_pts["travel"])
        last_pts = last_pts.rename(columns={"to_stop": "stop_gid"})
        last_pts = last_pts.drop(columns=["departure", "travel"])

        if geometry:
            last_pts["geometry"] = last_point(last_pts["geometry"])
        else:
            del last_pts["geometry"]

        points = pd.concat([points, last_pts], ignore_index=True)
        points = points.drop_duplicates(subset=(["line_gid", "stop_gid"]))

        if geometry:
            points = points.set_geometry("geometry", crs=self.data.crs)

        return points.sort_values(["line_gid", "stop_sequence"]).reset_index(drop=True)

    # ----------------------------------------------------------------------
    # filtering functions

    def filter_time_range(
        self, start: DayTime, end: DayTime, inclusive: str | None
    ) -> "Lines":
        """
        Return a new Lines object filtered between start and end times

        Arguments :
            start: int
                seconds from 00:00 on monday
            end : int
                seconds from 00:00 on monday
            inclusive : str
                include start, end, both or None
        """
        df = self.data.copy()

        # filter either departures and arrivals in range
        mask = df["departure"].listarray.is_between(
            start.to_seconds(), end.to_seconds(), inclusive
        )
        arrival = df["departure"].listarray.add(df["travel"])
        arr_mask = arrival.listarray.is_between(
            start.to_seconds(), end.to_seconds(), inclusive
        )
        mask = mask.listarray.or_(arr_mask)

        indices = df["departure"].listarray.inner_indices(as_list=True)
        _pos = indices.listarray.filter(mask.values)

        df["_start"] = _pos.listarray.aggregate("min")
        df["_end"] = _pos.listarray.aggregate("max")

        # drop line_gid with all arcs out of time range
        full_length = len(df)
        in_range = df.loc[
            (df._start.notna()) | (df._end.notna()), "line_gid"
        ].drop_duplicates()

        df = df.loc[df.line_gid.isin(in_range.values)].copy()
        transitlog.info(
            f"{full_length - len(in_range)} / {full_length} rows fully out of range"
        )

        # fill _start and _end with dummy value to find min and max with na
        dummy = df["_start"].max() + 1
        df = df.fillna({"_start": dummy, "_end": -1})

        # find maximum time range for each line_gid
        line_range = df.groupby("line_gid").agg(
            _start=("_start", "min"),
            _end=("_end", "max"),
        )
        df = df.loc[df.line_gid.isin(line_range.index)]
        df = pd.merge(
            df.drop(columns=["_start", "_end"]),
            line_range,
            left_on="line_gid",
            right_index=True,
            how="inner",
        )

        df["departure"] = df["departure"].listarray.slice(
            df["_start"], df["_end"], inclusive
        )
        df["travel"] = df["travel"].listarray.slice(df["_start"], df["_end"], inclusive)

        return Lines(df.drop(columns=["_start", "_end"]), self.year, self.week)


class Transfers:
    """Class to store transfer dataframe and metadata used to create it"""

    FILE = "transfers.parquet"

    def __init__(
        self,
        data: pd.DataFrame,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
        crs: str,
    ):
        self.data = data
        self.min_transfer = min_transfer
        self.max_transfer = max_transfer
        self.dists = dists
        self.crs = crs

    @classmethod
    def from_lines(
        cls,
        lines: pd.DataFrame,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
        crs: str,
    ) -> "Transfers":
        """init transfers from Lines and transfer parameters"""

        line_crs = lines.geometry.crs

        cols = [
            "line_gid",
            "route_type",
            "geometry",
            "stop_sequence",
            "departure",
            "travel",
            "direction_id",
            "route_gid",
        ]

        df = lines[cols].copy()
        df["_arrival"] = df["departure"].listarray.add(df["travel"])
        df["_arrival"] = df["_arrival"].listarray.add(min_transfer)
        df["_min_arr"] = df["_arrival"].listarray.aggregate("min")
        df["_max_arr"] = df["_arrival"].listarray.aggregate("max")
        df["_min_dep"] = df["departure"].listarray.aggregate("min")
        df["_max_dep"] = df["departure"].listarray.aggregate("max")

        # add distance column
        df["max_dist"] = cls.map_distance(df, dists)

        # transfer sources cannot be first stop of a line
        # if lines have just one arc, then stop_sequence cannot be 0
        single_arc = lines[["line_gid"]].groupby("line_gid").size()
        single_arc = single_arc.loc[single_arc == 1]
        mask = (df.stop_sequence > 0) & (~df.line_gid.isin(single_arc.index))
        sources = df.loc[mask].drop(
            columns=["_min_dep", "_max_dep", "departure", "travel"]
        )
        sources["geometry"] = last_point(sources.geometry)
        sources = sources.set_geometry("geometry", crs=line_crs).to_crs(crs)

        # transfer cannot target the last arc of a line
        mask = df.stop_sequence < LAST_STOP
        targets = df.loc[mask].drop(
            columns=["_arrival", "_min_arr", "_max_arr", "travel"]
        )
        targets["geometry"] = first_point(targets.geometry)
        targets = targets.set_geometry("geometry", crs=line_crs).to_crs(crs)
        targets = targets.rename(columns={"max_dist": "to_max_dist"})

        df = pairs(
            sources,
            "max_dist",
            targets,
            "to_max_dist",
            filter_distance="highest",
            drop_same="line_gid",
        )
        df = df.drop(columns=["max_dist", "to_to_max_dist"])
        _l = len(df)
        transitlog.info(f"{_l} base transfers")

        # drop not intersecting time ranges

        df = cls._drop_time_range(df)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as incompatible time ranges")

        # filter transfers
        df = cls._drop_duplicated_lines(df)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as duplicated to same lines")

        df = cls._drop_by_time(df, max_transfer)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as out of time range")

        # compact transfers by line_gid / stop_sequence source and target
        cols = ["line_gid", "stop_sequence", "to_line_gid", "to_stop_sequence"]
        grpcols = ["line_gid", "stop_sequence"]
        to_line = ls.list_array(df["to_line_gid"], df[grpcols], ignore_index=True)
        to_seq = ls.list_array(
            df["to_stop_sequence"], offsets=to_line, ignore_index=True
        )
        df = df[cols].drop_duplicates(subset=grpcols, ignore_index=True)
        df["to_line_gid"] = to_line
        df["to_stop_sequence"] = to_seq

        return cls(df.reset_index(drop=True), min_transfer, max_transfer, dists, crs)

    @staticmethod
    def _drop_duplicated_lines(pairs: pd.DataFrame) -> pd.DataFrame:
        """Drop duplicates from_line_gid / to_line_gid, keep closest by distance"""

        cols = ["line_gid", "stop_sequence", "to_line_gid"]
        df = pairs.sort_values(cols + ["distance"], ascending=True)
        df = df.drop_duplicates(subset=cols, keep="first")

        return df

    @staticmethod
    def _drop_by_time(pairs: pd.DataFrame, max_transfer: int) -> pd.DataFrame:
        """Drop transfers pairs if source and target timetables do no match, within min_transfer and max_transfer times"""

        df = pairs.copy()

        # remove pairs where the smallest transfer waits is too high
        df["_tr_time"] = df["_arrival"].listarray.match(df["to_departure"])

        diff = df["_tr_time"].listarray.subtract(df["_arrival"])
        diff = diff.listarray.aggregate("min")

        df = df.loc[diff <= max_transfer].copy()
        del df["_tr_time"]

        return df

    @staticmethod
    def _drop_time_range(pairs: pd.DataFrame) -> pd.DataFrame:
        mask = (pairs._max_arr < pairs.to__min_dep) | (
            pairs._min_arr > pairs.to__max_dep
        )
        df = pairs.loc[~(mask)].copy()
        df = df.drop(
            columns=[
                "_min_arr",
                "_max_arr",
                "to__min_dep",
                "to__max_dep",
            ]
        )

        return df

    @staticmethod
    def map_distance(df: pd.DataFrame, distance: DISTANCE_TYPE) -> pd.Series:
        """Return a distance column from distance argument, use maximum distance for missing route_types

        Arguments :
            distance:
                dictionary of distances by route_type or integer distance, in projected crs units

            Returns:
                a Series of distances"""

        if isinstance(distance, dict):
            if "route_type" not in df.columns:
                raise ValueError(
                    "If distance is a dict, route_type column must be present in lines"
                )
            dist = df["route_type"].map(distance)

            # replace missing route_types by maximum distance value
            dist.loc[dist.isna()] = max(distance.values())
            return dist
        elif isinstance(distance, (int, float)):
            return pd.Series(float(distance), index=df.index)
        else:
            raise ValueError("distance must be either a dict or a numeric value")

    @classmethod
    def from_parquet(
        cls,
        path: Path,
        crs: str,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
    ) -> "Transfers":
        """open parquet file"""
        filepath = path / cls.FILE
        try:
            table = pq.read_table(filepath)
        except (FileNotFoundError, OSError):
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to read parquet file '{filepath}': {e}") from e

        df = table.to_pandas(types_mapper=pd.ArrowDtype)
        return cls(df, min_transfer, max_transfer, dists, crs)

    def to_parquet(self, path: Path, name: Optional[str] = None):
        """save data to parquet at path with GTFS metadata"""
        table = pa.Table.from_pandas(self.data, index=False)
        metadata = table.schema.metadata
        metadata.update(self._gtfs_metadata())
        table = table.replace_schema_metadata(metadata)
        write_path = path / self.filename(name)
        pq.write_table(table, write_path)

    # -----------------------------------------------------------------------------------

    def expand_transfers(self, lines: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame of all transfer pairs with timetable information"""

        if self.data is None:
            raise ValueError("No transfers available, run add_transfers first")

        transf = self.data.explode(["to_line_gid", "to_stop_sequence"])

        # add timetable to upstream and downstream transfers
        transf = pd.merge(
            transf,
            lines[["line_gid", "stop_sequence", "departure", "travel"]],
            left_on=["line_gid", "stop_sequence"],
            right_on=["line_gid", "stop_sequence"],
            how="left",
        )

        transf["arrival"] = transf["departure"].listarray.add(transf["travel"])
        transf = transf.drop(columns=["travel", "departure"])

        transf = pd.merge(
            transf,
            lines[["line_gid", "stop_sequence", "departure"]],
            left_on=["to_line_gid", "to_stop_sequence"],
            right_on=["line_gid", "stop_sequence"],
            how="left",
            suffixes=("", "_to"),
        )

        transf = transf.rename(columns={"departure": "to_departure"})
        transf = transf.drop(columns=["line_gid_to", "stop_sequence_to"])
        transf = transf.reset_index(drop=True)

        # drop transfers with non matching timetables
        transf["_threshold"] = transf["arrival"].listarray.add(self.min_transfer)
        mask = transf["_threshold"].listarray.intersects(transf["to_departure"])
        transf = transf.loc[mask].copy()

        # corresponding departure timepoints array
        transf["to_timepoint"] = transf["_threshold"].listarray.match(
            transf["to_departure"]
        )
        del transf["_threshold"]

        # filter NA to_timepoint and array
        na_mask = transf["to_timepoint"].listarray.isna()
        transf["to_timepoint"] = transf["to_timepoint"].listarray.filter(na_mask)
        transf["arrival"] = transf["arrival"].listarray.filter(na_mask)
        transf = transf.dropna(subset="to_timepoint")

        return transf.reset_index(drop=True)


# ------------------------------------------------------------------------------------------------------
# Transit class


class Transit:
    """GTFS parquet data in a 3 main dataframes.

    Attributes:
        crs : str
            projection used to store geometries
        lines : Lines
            transit lines object containing lines dataframe and metadata
        stops : Stops
            transit stops object containing stops dataframe and metadata
        agencies : Agencies
            transit agencies object containing agencies dataframe and metadata
        transfers: Transfers
            optional transfers object containing transfers dataframe and metadata
    """

    def __init__(
        self,
        crs: str,
        lines: Lines,
        stops: Stops,
        agencies: Agencies,
        transfers: Optional[Transfers] = None,
        on_demand: bool = True,
    ):
        self.crs = crs
        self.lines = lines
        self.stops = stops
        self.agencies = agencies
        self.transfers = transfers

        if lines is not None:
            self.year = self.lines.year
            self.week = self.lines.week
        else:
            self.year = None
            self.week = None

        if self.lines is not None:
            self._sync_agencies_to_lines()
            self._sync_stops_to_lines()

    @classmethod
    def from_parquet_dataset(
        cls, path: str, crs: str, year: Optional[int] = None, week: Optional[int] = None
    ) -> "Transit":
        lines = Lines.from_parquet_dataset(path, year=year, week=week)
        stops = Stops.from_parquet_dataset(path, year=year, week=week)
        agencies = Agencies.from_parquet_dataset(path, year=year, week=week)
        return cls(crs, lines, stops, agencies)

    @classmethod
    def from_parquet_dir(
        cls, path: str, crs: str, year: Optional[int] = None, week: Optional[int] = None
    ) -> "Transit":
        lines = Lines.from_parquet(path, year=year, week=week)
        stops = Stops.from_parquet(path, year=year, week=week)
        agencies = Agencies.from_parquet(path, year=year, week=week)
        transfers = Transfers.from_parquet(path, crs=crs, year=year, week=week)
        return cls(crs, lines, stops, agencies, transfers)

    def to_parquet(self, save_path: str):
        """Save to Parquet"""
        self.lines.to_parquet(save_path)
        self.stops.to_parquet(save_path)
        self.agencies.to_parquet(save_path)
        if self.transfers is not None:
            self.transfers.to_parquet(save_path)
        return None

    # ----------------------------------------------------------------------
    # synchronize ids between dataframe functions

    def _sync_agencies_to_lines(self):
        """Synchronize agencies and routes to the ones in lines dataframe"""
        # synchronize agencies to only the ones in lines
        ids = self.lines.data["agency_gid"]
        self.agencies = self.agencies.filter_gids(ids)

        # synchronize routes in agencies to only the ones in lines
        ids = self.lines.data["route_gid"]
        self.agencies = self.agencies.filter_routes(ids)

        return None

    def _sync_lines_to_agencies(self):
        """Synchronize lines to route_gid"""

        route_gids = self.agencies.routes().index.drop_duplicates()
        self.lines = self.lines.filter_routes(route_gids)
        return None

    def _sync_stops_to_lines(self):
        """Synchronize stops to the ones in lines dataframe"""
        ids = self.lines.line_stops()["stop_gid"].drop_duplicates()
        self.stops = self.stops.filter_gids(ids.values)
        return None

    # ----------------------------------------------------------------------
    # filtering functions

    def filter_time(self, start: DayTime, end: DayTime, inclusive="both"):
        """
        Filter the lines between start and end times

        Arguments :
            start: int
                seconds from 00:00 on monday
            end : int
                seconds from 00:00 on monday
            inclusive : str
                include start, end, both or None

            Returns:
                None"""

        self.lines = self.lines.filter_time_range(
            start.to_seconds(), end.to_seconds(), inclusive
        )

        # invalidate transfers
        self.transfers = None
        self._sync_agencies_to_lines()
        self._sync_stops_to_lines()

        return None

    def filter_box(self, bounding_box: Tuple[float, float, float, float]):
        """
        Filter by bounding box

        Arguments:
            bouding_box : tuple of floats
                latmin, latmax, lonmin, lonmax

        Returns :
            None"""

        _bbox = self.agencies.routes()["bbox"]
        xmin, xmax, ymin, ymax = bounding_box

        x_mask = _bbox.struct.field("xmin").between(xmin, xmax) | _bbox.struct.field(
            "xmax"
        ).between(xmin, xmax)
        y_mask = _bbox.struct.field("ymin").between(ymin, ymax) | _bbox.struct.field(
            "ymax"
        ).between(ymin, ymax)

        route_gids = _bbox.loc[x_mask & y_mask].index.copy()

        # filter routes
        self.agencies = self.agencies.filter_routes(route_gids)

        # synchronise lines and stops
        self._sync_lines_to_agencies()
        self._sync_stops_to_lines()

        return None

    # ----------------------------------------------------------------------
    # transfers functions
    def add_transfers(self, min_transfer: int, max_transfer: int, dist: DISTANCE_TYPE):
        """
        Create transfers

        Arguments :
            min_transfer: int
                minimum time between transfers (seconds)
            max_transfer: int
                maximum time between transfers (seconds)
            dict: dict or int
                distance or dictionary route_type:distance in crs unit,
                use longest distance for missing route types

        Returns:
            None"""
        self.transfers = Transfers.from_lines(
            self.lines.data, min_transfer, max_transfer, dist, self.crs
        )

    # ----------------------------------------------------------------------
    # Graph creation

    def graph(self):
        """Return a Graph object"""
        return TransitGraph(self.lines, self.transfers, self.crs)

    # ----------------------------------------------------------------------
    # Statistics
    @staticmethod
    def _stop_stats_agg(df, aggregators, groupby, col="time"):
        grped = df.groupby(groupby, sort=True)
        stats = grped[col].agg(aggregators)
        stats.loc[:, "nb"] = grped.size().astype("int32[pyarrow]")
        return stats

    @staticmethod
    def flatten_columns(df, separator="_"):
        """flatten multi index columns with separator"""

        flatten = df.columns.to_flat_index()
        columns = []
        for col in flatten:
            if isinstance(col, str):
                columns.append(col)
            elif isinstance(col, tuple):
                columns.append(separator.join(col))
            else:
                raise ValueError("columns must string or tuple")
        df.columns = columns
        return df

    def stop_statistics(self, cutoff_hour=None):
        """return stop statistics per day: first and last time, number of trips. First time after cutoff hour (i.e. start at 3 AM)"""

        if not (
            cutoff_hour is None
            or (isinstance(cutoff_hour, int) and cutoff_hour > 0 and cutoff_hour < 24)
        ):
            raise ValueError("cutoff_hour must be None or an integer between 0 and 23")

        df = self.lines.line_stops()[
            [
                "stop_gid",
                "time",
                "route_gid",
                "direction_id",
                "agency_gid",
                "line_gid",
                "name",
                "route_type",
            ]
        ]

        # expand times and find day
        expanded = df[["time", "stop_gid", "route_gid", "direction_id"]].explode("time")
        expanded["day"] = day_from_seconds(expanded["time"])

        grp = ["stop_gid", "route_gid", "direction_id", "day"]

        if cutoff_hour is None:
            stats = self._stop_stats_agg(expanded, ["min", "max"], grp)

        else:
            start = cutoff_hour * 3600
            end = 3600 * (24 * 7 + cutoff_hour)
            day_min = dict(zip(WEEKDAYS, range(start, end, 3600 * 24)))
            expanded["_cutoff"] = expanded["day"].map(day_min)

            # separate stats for times above and below cutoff
            mask = expanded["time"] >= expanded._cutoff
            stats = self._stop_stats_agg(
                expanded.loc[mask], ["min", "max"], groupby=grp
            )

            mask = expanded["time"] < expanded._cutoff
            new_stats = self._stop_stats_agg(
                expanded.loc[mask], ["max"], groupby=grp
            ).dropna()
            stats.loc[stats.index.isin(new_stats.index), ["max", "nbmax"]] = new_stats
            stats["nb"] = stats["nb"].fillna(0) + stats["nbmax"].fillna(0)
            stats["nb"] = stats["nb"].astype("int32[pyarrow]")
            del stats["nbmax"]

        stats["amplitude"] = stats["max"] - stats["min"]
        stats["amplitude"] = stats["amplitude"].div(60).round().astype("int32[pyarrow]")
        stats.loc[stats.amplitude < 0, "amplitude"] = stats["amplitude"] + 24 * 60

        stats["min"] = seconds_to_text(stats["min"], year=self.year, week=self.week)
        stats["max"] = seconds_to_text(stats["max"], year=self.year, week=self.week)

        # add route data
        routes = self.agencies.routes(agency_data=True)[
            ["name", "agency_name", "route_type"]
        ]
        stats = pd.merge(stats.reset_index(), routes, on="route_gid", how="left")

        # add stop geometry and name

        stats = stats.set_index("stop_gid", drop=True)
        stops = self.stops.data[["geometry", "stop_name"]]
        stats = pd.merge(stats, stops, left_index=True, right_index=True, how="left")

        stats = stats.set_geometry("geometry", crs=self.stops.data.crs)
        return stats

    def clusters(self, max_distance: int, min_samples: int) -> pd.DataFrame:
        """Return polygon with clusters of stops, within max_distance and min_samples size"""

        line_stops = self.lines.line_stops(geometry=True)[
            ["geometry", "route_gid", "stop_gid"]
        ]
        geoms = (
            line_stops[["stop_gid", "geometry"]]
            .drop_duplicates("stop_gid")
            .set_geometry("geometry")
        )
        geoms = geoms.to_crs(self.crs)
        coords = geoms.get_coordinates()

        cluster = DBSCAN(eps=max_distance, min_samples=min_samples).fit(
            coords.to_numpy()
        )
        df = pd.Series(cluster.labels_, index=coords.index).astype("int32[pyarrow]")
        df = df.to_frame("cluster_id")
        df["cluster_core"] = df.index.isin(cluster.core_sample_indices_)
        df.loc[df.cluster_id == -1, "cluster_id"] = pd.NA

        # merge with stops data
        df = line_stops.join(df, how="left")

        # cluster size without duplicated columns
        cols = ["cluster_id", "route_gid"]
        _size = (
            df.drop_duplicates(cols)
            .groupby("cluster_id")
            .size()
            .astype("int32[pyarrow]")
        )
        _size = _size.loc[_size >= min_samples]

        df = pd.merge(
            df,
            _size.to_frame("cluster_size"),
            left_on="cluster_id",
            right_index=True,
            how="left",
        )
        df.loc[df["cluster_size"].isna(), ["cluster_id", "cluster_core"]] = pd.NA

        df["cluster_core"] = df["cluster_core"].astype(pd.ArrowDtype(pa.bool_()))

        # create cluster geometry
        df = df.dissolve("cluster_id").to_crs(self.crs)[["geometry", "cluster_size"]]
        df["geometry"] = df.convex_hull.buffer(max_distance / 10.0)

        return df.reset_index(drop=True).set_geometry("geometry")
