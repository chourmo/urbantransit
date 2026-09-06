from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
from sklearn.cluster import DBSCAN

from ..utils.time import WEEKDAYS, DayTime, day_from_seconds, seconds_to_text
from .agencies import Agencies
from .lines import Lines
from .stops import Stops
from .transfers import DISTANCE_TYPE, Transfers

if TYPE_CHECKING:
    from ..graph import TransitGraph

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
        transfers: Transfers | None = None,
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
        cls, path: str, crs: str, year: int | None = None, week: int | None = None
    ) -> "Transit":
        lines = Lines.from_parquet_dataset(path, year=year, week=week)
        stops = Stops.from_parquet_dataset(path, year=year, week=week)
        agencies = Agencies.from_parquet_dataset(path, year=year, week=week)
        return cls(crs, lines, stops, agencies)

    @classmethod
    def from_parquet_dir(
        cls, path: str, crs: str, year: int | None = None, week: int | None = None
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

    def _sync_lines_to_agencies(self):
        """Synchronize lines to route_gid"""

        route_gids = self.agencies.routes().index.drop_duplicates()
        self.lines = self.lines.filter_routes(route_gids)

    def _sync_stops_to_lines(self):
        """Synchronize stops to the ones in lines dataframe"""
        ids = self.lines.line_stops()["stop_gid"].drop_duplicates()
        self.stops = self.stops.filter_gids(ids.values)

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

    def filter_box(self, bounding_box: tuple[float, float, float, float]):
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

    def graph(self) -> "TransitGraph":
        """Return a Graph object"""
        from ..graph import TransitGraph

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
                raise TypeError("columns must string or tuple")
        df.columns = columns
        return df

    def stop_statistics(self, cutoff_hour=None):
        """return stop statistics per day: first and last time, number of trips. First time after cutoff hour (i.e. start at 3 AM)"""

        if not (
            cutoff_hour is None
            or (isinstance(cutoff_hour, int) and cutoff_hour > 0 and cutoff_hour < 24)
        ):
            raise TypeError("cutoff_hour must be None or an integer between 0 and 23")

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
