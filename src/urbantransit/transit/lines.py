from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.dataset as ds
from geometryhelpers import first_point, last_point

from ..constants import LAST_STOP
from ..utils.ids import filter_ids
from ..utils.logging import transitlog
from ..utils.time import DayTime


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
    def from_parquet_dataset(
        cls, path: str | Path, year: int, week: int
    ) -> "Lines":
        """Create Lines from a dataset for year and week."""

        file_path = Path(path) / cls.DATASET

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
    def from_parquet(cls, path: str | Path, year: int, week: int) -> "Lines":
        """Create Lines from a directory."""

        file_path = Path(path) / cls.FILE

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

    def to_parquet(self, path: str | Path):
        """Save to geoparquet file."""

        path = Path(path)

        # save index if it has a name
        if self.data.index.name is not None:
            data = self.data.reset_index()
        else:
            data = self.data

        data.to_parquet(path / self.FILE, index=False)

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
