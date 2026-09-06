# GTFS to Parser maping class
from pathlib import Path

import geopandas as gpd
import listandstruct as ls
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
from geometryhelpers import (
    Linestrings,
    Points,
    connect_points,
    first_point,
    group_boundingbox,
    last_point,
)

from urbantransit.utils.ids import hash_ids, intersect_ids, random_ids, renumber_ids
from urbantransit.utils.logging import transitlog

from .constants import LAST_STOP
from .parsers import GTFSParser


class GTFStoParquet:
    """Object containing gtfs data in a 3 main dataframes : agency, places (stops and stations) and lines.
    Extra attributes are the ones used for data parsing : file path, year and week
    """

    def __init__(self, path: Path, crs: str, week: int, year: int):
        self.week = week
        self.year = year
        self.crs = crs

        self.path = path
        self.name = path.stem

        parser = GTFSParser(self.path)
        self.agencies = self.parse_agency(parser)
        self.feedinfo = self.parse_feedinfo(parser)

        self.routes = self.parse_routes(parser)
        self.stops, self.stations = self.parse_stops(parser)
        self.lines = self.parse_lines(parser)

        # force existence between dataframes
        # filter routes from lines and add boundingbox
        self.routes, self.lines = intersect_ids(
            self.routes.reset_index(),
            self.lines,
            left_id="route_gid",
            right_id="route_gid",
        )

        self.routes = self.routes.set_index("route_gid")
        self.routes["bbox"] = self.route_bboxes()

        # clean agency and stops from values not in lines
        agencies, _ = intersect_ids(
            self.agencies.reset_index(),
            self.routes,
            left_id="agency_gid",
            right_id="agency_gid",
        )
        self.agencies = agencies.set_index("agency_gid")
        self.agencies["bbox"] = self.agency_bbox()

        # drop unused stops
        stop_gids = pd.concat(
            [self.lines["from_stop"], self.lines["to_stop"]], ignore_index=True
        ).drop_duplicates()

        self.stops = self.stops.loc[self.stops.index.isin(stop_gids)]

    # -----------------------------
    # save to parquet

    @staticmethod
    def _init_path(path, year, week):
        if not path.is_dir():
            path.mkdir()

        name = "year=" + str(year)
        new_path = path / name
        if not new_path.is_dir():
            new_path.mkdir()

        name = "week=" + str(week)
        new_path = new_path / name
        if not new_path.is_dir():
            new_path.mkdir()

        return new_path

    def init_parquet_paths(self, path: Path) -> tuple[Path, Path, Path]:
        """init subpath in path, return paths for Agency, Stops and Lines"""

        line_path = path / "Lines"
        line_path = self._init_path(line_path, self.year, self.week)

        agency_path = path / "Agencies"
        agency_path = self._init_path(agency_path, self.year, self.week)

        stop_path = path / "Stops"
        stop_path = self._init_path(stop_path, self.year, self.week)

        return agency_path, stop_path, line_path

    @staticmethod
    def new_file_path(path: Path, separator="_", name="data") -> Path:
        """create a new filepath in a directory"""

        # find all file integers
        names = [f.stem.split(separator) for f in path.glob("*.parquet")]
        names = [
            int(x[1])
            for x in names
            if (len(x) == 2 and x[1].isdigit() and x[0] == name)
        ]

        if len(names) > 0:
            index = max(names) + 1
        else:
            index = 0
        filename = name + separator + str(index) + ".parquet"
        new_path = path / filename
        return new_path

    @staticmethod
    def pack_routes(routes: pd.DataFrame) -> pd.DataFrame:
        """Pack routes in ListArray of ls.."""

        packed = routes.sort_values("agency_gid").copy()

        if packed.index.name == "route_gid":
            packed = packed.reset_index()

        packed["routes"] = ls.struct_array(packed.drop(columns=["agency_gid"]))
        packed = packed.sort_values("agency_gid")[["routes", "agency_gid"]]
        packed = ls.list_array(
            packed["routes"], packed["agency_gid"], ignore_index=False
        )

        return packed.to_frame("routes")

    def agencies_to_parquet(self, path: Path) -> None:
        """Save agencies, routes and feed_info to Parquet at Path."""

        routes = self.pack_routes(self.routes)

        if "routes" in self.agencies.columns:
            agencies = self.agencies.drop(columns=["routes"])
        else:
            agencies = self.agencies

        agencies = agencies.join(routes, how="left")

        if len(self.feedinfo) > 0:
            feedinfo = self.feedinfo.loc[self.feedinfo.index.repeat(len(agencies))]
            feedinfo.index = agencies.index
            agencies.loc[:, feedinfo.columns] = feedinfo

        agencies.to_parquet(self.new_file_path(path), index=True)

    def stops_to_parquet(self, path: Path) -> None:
        """ " Save stops to parquet file at Path."""
        self.stops.to_parquet(self.new_file_path(path), index=True)

    def lines_to_parquet(self, path: Path) -> None:
        """ " Save lines to parquet file at Path."""
        self.lines.to_parquet(self.new_file_path(path), index=False)

    def to_parquet(self, path: Path):
        """Save to parquet"""

        agency_path, stop_path, line_path = self.init_parquet_paths(path)

        self.agencies_to_parquet(agency_path)
        self.stops_to_parquet(stop_path)
        self.lines_to_parquet(line_path)

    # -----------------------------
    # merge other GTFStoParquet object

    @staticmethod
    def _merge_file(
        df: pd.DataFrame | pd.Series,
        other: pd.DataFrame | pd.Series,
        column: str | None = None,
    ) -> tuple[pd.DataFrame | pd.Series, pd.Series]:
        """Append other to df, renumber indices of other to avoid collisions, return new dataframe or series and the mapping from old to new indices for other"""

        new_other = other.copy()

        if column is not None and column in other.columns and column in df.columns:
            remap = other[column]
            mapper = renumber_ids(remap, df[column])
        elif column is None:
            remap = pd.Series(other.index)
            mapper = renumber_ids(remap, pd.Series(df.index))
        else:
            raise ValueError("Column must be in both dataframes or None to remap index")

        if len(mapper) == 0:
            result = pd.concat([df, other], ignore_index=False)
            return result, mapper

        remap = remap.map(mapper).fillna(remap)

        if column is None:
            new_other.index = remap.values
        else:
            new_other[column] = remap

        result = pd.concat([df, new_other], ignore_index=False)
        result.index.name = df.index.name

        return result, mapper

    def merge(self, other: "GTFStoParquet") -> None:
        """Merge another GTFStoParquet object into this one, renumbering ids to avoid collisions"""

        # merge agencies
        self.agencies, agency_mapper = self._merge_file(self.agencies, other.agencies)
        self.stops, stop_mapper = self._merge_file(self.stops, other.stops)
        self.routes, route_mapper = self._merge_file(self.routes, other.routes)

        # merge lines
        other_lines = other.lines.copy()

        # remap agency_gid in stops and routes
        if len(agency_mapper) > 0:
            transitlog.info(
                f"Renumbering {len(agency_mapper)} agency values to avoid collisions"
            )
            self.stops["agency_gid"] = (
                self.stops["agency_gid"]
                .map(agency_mapper)
                .fillna(self.stops["agency_gid"])
            )
            self.routes["agency_gid"] = (
                self.routes["agency_gid"]
                .map(agency_mapper)
                .fillna(self.routes["agency_gid"])
            )
            other_lines["agency_gid"] = (
                other_lines["agency_gid"]
                .map(agency_mapper)
                .fillna(other_lines["agency_gid"])
            )

        # remap from_stop, to_stop and route_gid in lines to match new ids
        if len(stop_mapper) > 0:
            transitlog.info(
                f"Renumbering {len(stop_mapper)} stop values to avoid collisions"
            )
            other_lines["from_stop"] = (
                other_lines["from_stop"]
                .map(stop_mapper)
                .fillna(other_lines["from_stop"])
            )
            other_lines["to_stop"] = (
                other_lines["to_stop"].map(stop_mapper).fillna(other_lines["to_stop"])
            )

        if len(route_mapper) > 0:
            transitlog.info(
                f"Renumbering {len(route_mapper)} route values to avoid collisions"
            )
            other_lines["route_gid"] = (
                other_lines["route_gid"]
                .map(route_mapper)
                .fillna(other_lines["route_gid"])
            )

        self.lines, _ = self._merge_file(self.lines, other_lines, column="line_gid")

    # -----------------------------
    # FeedInfo.txt parser

    def parse_feedinfo(self, parser: GTFSParser) -> pd.DataFrame:
        """Return a dataframe with optionnal feed_info"""
        feedinfo = parser.get_feedinfo()
        return feedinfo.data

    # -----------------------------
    # Agency.txt parser

    def agency_bbox(self) -> pd.Series:
        """return the bounding box of all lines per agency_gid"""
        if self.lines is None:
            raise ValueError("Lines must be parsed before adding agency bounding boxes")

        return group_boundingbox(self.lines, groupby="agency_gid")

    def parse_agency(self, parser: GTFSParser) -> pd.DataFrame:
        """Reads the agency.txt file from the GTFS zip file and returns a DataFrame"""

        agency = parser.get_agency().data

        # create unique gid based on name and timezone
        agency["agency_gid"] = hash_ids(
            agency, columns=["agency_name", "agency_timezone"]
        )

        # Agency_id is optional, fill with agency_name
        agency.loc[agency.agency_id.isna(), "agency_id"] = agency["agency_name"]

        return agency.set_index("agency_gid")

    # -----------------------------
    # Routes.txt parser

    def parse_routes(self, parser: GTFSParser) -> pd.DataFrame:
        """Returns a DataFrame grouped by agency"""

        routes = parser.get_routes().data

        # agency_id may not be set if only one agency, set agency_id value
        if len(routes.loc[routes.agency_id.isna()]) > 0:
            routes["agency_id"] = self.agency["agency_id"].head(0)

        # create route name
        routes["name"] = routes["route_short_name"]
        routes.loc[routes.name.isna(), "name"] = routes["route_long_name"]
        routes.loc[routes.route_short_name.notna(), "route_long_name"] = pd.NA
        del routes["route_short_name"]

        # change continuous_ to routes_ to avoid name duplication with stop_times values
        if "continuous_drop_off" in routes.columns:
            routes = routes.rename(columns={"continuous_drop_off": "routes_drop_off"})
        if "continuous_pickup" in routes.columns:
            routes = routes.rename(columns={"continuous_pickup": "routes_pickup"})

        # unique gid for routes
        routes["route_gid"] = hash_ids(routes, ["agency_id", "route_id"])

        if self.agencies is None:
            raise ValueError("Agencies must be parsed before routes")

        # add agency_gid
        routes = pd.merge(
            routes,
            self.agencies.reset_index()[["agency_gid", "agency_id", "agency_name"]],
            on="agency_id",
            how="left",
        )

        routes = routes.drop(columns=["route_sort_order", "agency_id"])
        return routes.set_index("route_gid")

    def route_bboxes(self) -> pd.Series:
        """return a dataframe with route_gid as index and bbox struc values"""
        if self.lines is None:
            raise ValueError("Lines must be parsed before adding route bounding boxes")

        return group_boundingbox(self.lines, groupby="route_gid")

    # -----------------------------
    # Stops.txt parser

    def stations(self, stations: pd.DataFrame) -> pd.DataFrame:
        """convert stops to stations"""
        return stations

    def simple_stops(self, stops: pd.DataFrame) -> pd.DataFrame:
        """create simple stops"""
        df = stops.copy()
        df.loc[:, "stop_gid"] = random_ids(len(df))

        df.loc[:, "geometry"] = Points(stops.stop_lon, stops.stop_lat, crs=4326)
        df = df.set_index("stop_gid")
        df = df.drop(columns=["location_type", "stop_lat", "stop_lon"])
        df = df.set_geometry("geometry", crs=4326)

        return df

    def parse_stops(self, parser: GTFSParser) -> tuple[pd.DataFrame, pd.DataFrame]:
        df = parser.get_stops().data
        levels = parser.get_levels()

        if levels.empty:
            df.loc[:, "level_index"] = levels.data["level_index"]
            df.loc[:, "level_name"] = levels.data["level_name"]
        else:
            df = pd.merge(df, levels.data, on="level_id")
            df = df.drop(columns="level_id")

        stations = self.stations(df.loc[df.location_type > 0])
        stops = self.simple_stops(df.loc[df.location_type == 0])

        # TODO : update stop_locations in stops

        return stops, stations

    # ----------------------------------------------------------------------
    # Trips.txt functions

    @staticmethod
    def time_for_day(day: pd.Series) -> pd.Series:
        return 24 * 3600 * day.dt.weekday

    def parse_trips(self, parser: GTFSParser) -> pd.DataFrame:
        """Parse trips and add calendar/calendar_dates days for week"""

        trips = parser.get_trips().data
        services = parser.merged_calendars()

        if len(services) == 0:
            raise ValueError(
                f"{self.name} has no service for {self.week}/{self.year} week"
            )

        # filter for week and year
        services = services.loc[
            (services.week == self.week) & (services.year == self.year)
        ]

        # map day to time
        services["time"] = self.time_for_day(services["date"])

        # expand trips
        trips = trips.merge(services, how="right", on="service_id")

        if "trip_short_name" in trips.columns:
            del trips["trip_short_name"]

        # create a ListArray of times per service_id
        trips = trips.sort_values(["trip_id", "time"], ascending=True)
        df = trips.drop_duplicates(subset="trip_id", keep="first", ignore_index=True)
        df = df.drop(columns=["time", "week", "year", "service_id", "date"])
        df.loc[:, "time"] = ls.list_array(
            trips["time"], ids=trips["trip_id"], ignore_index=True
        )

        # add routes columns
        cols = [
            "name",
            "route_id",
            "route_type",
            "agency_gid",
            "routes_pickup",
            "routes_drop_off",
            "agency_name",
        ]
        df = df.merge(self.routes[cols].reset_index(), on="route_id")
        df = df.drop(columns=["route_id", "block_id"])

        return df

    # ----------------------------------------------------------------------
    # Shapes.txt functions

    @staticmethod
    def correct_short_shapedist(
        shapes: pd.DataFrame, lines: pd.DataFrame, float_correction=1.0001
    ) -> pd.DataFrame:
        """correct shapedist lower than max shapedist in trips"""

        # find max shape_dist in shapes and trips
        shape_max = shapes["shape_dist"].listarray.aggregate("max")
        shape_max = shape_max / float_correction

        df = lines.copy()
        df["_max"] = df.groupby("shape_id")["shape_dist"].transform("max")
        df = pd.merge(
            df,
            shape_max.to_frame("_shpmax"),
            left_on="shape_id",
            right_index=True,
            how="left",
        )
        df["_max"] = df["_max"].fillna(1)
        df["_shpmax"] = df["_shpmax"].fillna(1)

        # correct ratio
        df.loc[df._max > df._shpmax, "shape_dist"] = (
            df["shape_dist"] * df._shpmax / df._max
        )
        return df.drop(columns=["_max", "_shpmax"])

    def parse_shapes(
        self, parser: GTFSParser, shape_ids: pd.Series = None
    ) -> pd.DataFrame:
        """Parse shapes.txt, fill optional shape_dist, select subset of shape_ids if specified, pack in ListArrays and create geometry"""

        shapes = parser.get_shapes()
        if shapes.empty:
            return None

        if shape_ids is not None:
            shapes.filter_shape_ids(shape_ids)

        # drop shapes with only one point
        shapes.drop_single_point()
        shapes.fill_shape_dist()

        cols = {
            "shape_dist_traveled": "shape_dist",
            "shape_pt_lon": "lon",
            "shape_pt_lat": "lat",
        }
        shapes = shapes.data.rename(columns=cols)

        # pack in ListArrays and create geometry
        df = (
            shapes[["shape_id"]]
            .drop_duplicates(subset="shape_id")
            .reset_index(drop=True)
        )
        df["shape_dist"] = ls.list_array(
            shapes["shape_dist"], ids=shapes["shape_id"], ignore_index=True
        )
        offsets = df["shape_dist"].listarray.offsets
        df["lon"] = ls.list_array(shapes["lon"], offsets=offsets, ignore_index=True)
        df["lat"] = ls.list_array(shapes["lat"], offsets=offsets, ignore_index=True)
        geoms = Linestrings(shapes["lon"], shapes["lat"], shapes["shape_id"], crs=4326)[
            "geometry"
        ]
        df["geometry"] = geoms.reset_index(drop=True)

        return df.set_geometry("geometry").set_index("shape_id")

    # ----------------------------------------------------------------------
    # Stoptimes.txt functions

    @staticmethod
    def normalize_sequence(sequence: pd.DataFrame) -> pd.DataFrame:
        """Update stop_sequences to normalized form : starts at 0, increment by 1, last stop is 65535."""
        df = sequence.to_frame("seqid").copy()
        df["seq"] = 1
        df["seq"] = df.groupby("seqid")["seq"].cumsum() - 1
        df["max"] = df.groupby("seqid")["seq"].transform("max")
        df.loc[df["max"] == df.seq, "seq"] = LAST_STOP

        return df["seq"].astype(pd.ArrowDtype(pa.uint16()))

    @staticmethod
    def to_seconds(df):
        """Return seconds from midnight for a time string in the format HH:MM:SS or H:MM:SS. Handle times higher than 24:00:00."""
        array = pa.array(df, from_pandas=True, type=pa.string())

        # first 0 is optional per spec, force to 8 caracters
        array = pc.ascii_lpad(array, 8, "0")
        array = pc.binary_replace_slice(array, start=2, stop=3, replacement=b"")
        array = pc.binary_replace_slice(array, start=4, stop=5, replacement=b"")

        array = pc.cast(array, pa.int64(), safe=False)

        hours = pc.floor(pc.divide(array, 10000))
        minutes = pc.floor(
            pc.divide(pc.subtract(array, pc.multiply(hours, 10000)), 100)
        )
        sec = pc.subtract(
            array, pc.add(pc.multiply(hours, 10000), pc.multiply(minutes, 100))
        )

        result = pc.add(sec, pc.add(pc.multiply(hours, 3600), pc.multiply(minutes, 60)))

        return pd.Series(result, df.index, dtype="uint32[pyarrow]")

    @staticmethod
    def get_line_id(sequence: pd.DataFrame, name: str | None = None) -> pd.Series:
        """Map each seq_id to a line_gid.
        A line_gid groups trips with the same stop pattern and non-overlapping times.
        """

        cols = ["seq_id", "stop_gid", "stop_sequence", "departure", "arrival"]
        df = sequence[cols].copy()

        # Build a stable stop-pattern signature (same logic, lighter intermediate state)
        codes, _ = pd.factorize(df["stop_gid"], sort=False)
        pos_stop = (
            (pd.Series(codes, index=df.index) * 100000)
            .div(1 + df["stop_sequence"])
            .round(0)
            .astype("int64[pyarrow]")
        )

        # Aggregate once by seq_id
        departure = ls.list_array(df["departure"], df["seq_id"], ignore_index=True)
        arrival = ls.list_array(df["arrival"], offsets=departure, ignore_index=True)
        pos = ls.list_array(pos_stop, offsets=departure, ignore_index=True)

        lines = df[["seq_id"]].drop_duplicates(ignore_index=True)
        lines["line_gid"] = pos.listarray.aggregate("sum")
        lines["arrival"] = arrival
        lines["departure"] = departure
        lines["_first_dep"] = lines["departure"].listarray.get(0)

        # Split conflicting schedules (overlapping times inside same line_gid)
        while True:
            lines = lines.sort_values(["line_gid", "_first_dep"], ascending=True)

            prev_arrival = lines.groupby("line_gid", sort=False)["arrival"].shift(1)
            prev_arrival = prev_arrival.where(prev_arrival.notna(), lines["arrival"])

            min_diff = (
                lines["departure"]
                .listarray.subtract(prev_arrival)
                .listarray.aggregate("min")
            )
            conflict = min_diff < 0

            if not conflict.any():
                break

            bad_line_gids = lines.loc[conflict, "line_gid"].drop_duplicates().to_list()
            next_gid = int(lines["line_gid"].max()) + 1
            remap = dict(
                zip(bad_line_gids, range(next_gid, next_gid + len(bad_line_gids)))
            )
            lines.loc[conflict, "line_gid"] = lines.loc[conflict, "line_gid"].map(remap)

        line_gids = lines["line_gid"].nunique()
        route_dirs = sequence[["route_gid", "direction_id"]].drop_duplicates().shape[0]
        transitlog.info(
            f"{name} : {round(line_gids / route_dirs, 2)} lines per route/direction"
        )

        return lines.set_index("seq_id")["line_gid"]

    @staticmethod
    def is_invalid_dist(distance: pd.Series, sequence: pd.DataFrame) -> pd.Series:
        """test if shape_dist_travelled is always increasing or na"""

        mask_next = distance > distance.shift(-1).fillna(0)
        mask_prev = distance < distance.shift(1).fillna(0)

        # case 1 : distance is na, set to invalid
        m1 = distance.isna()

        # case 2 : sequence is neither 0 or last point, compare to previous and next distance
        m2 = (sequence.between(0, LAST_STOP, "neither")) & ((mask_next) | (mask_prev))

        # case 3 : first point and next distance lower
        m3 = (sequence == 0) & (mask_next)

        # case 3 : last point and previous distance higher
        m4 = (sequence == LAST_STOP) & (mask_prev)

        return m1 | ((distance.notna()) & ((m2) | (m3) | (m4)))

    def parse_stoptimes(self, parser: GTFSParser, trip_ids: pd.Series) -> pd.DataFrame:
        """Parse Stop Times whose trips_ids are in trip_ids, add seq_id normalize headsign, stop_sequence and shape_dist"""

        stops = parser.get_stoptimes()

        # fix invalid stop_times
        stops.filter_trips(trip_ids)

        stops.set_sequence_id("trip_id", "seq_id")
        stops.fill_missing_times()
        stops.drop_duplicate_stops()
        stops.drop_single_sequence()
        stops.drop_invalid_shapedist()

        stops = stops.data
        stops = stops.rename(columns={"shape_dist_traveled": "shape_dist"})

        # convert arrival_time and departure_time to seconds
        stops["arrival"] = self.to_seconds(stops["arrival_time"])
        stops["departure"] = self.to_seconds(stops["departure_time"])
        stops = stops.drop(columns=["departure_time", "arrival_time"])

        # stops.loc[:, "seq_id"] = self.sequence_id(stops)
        stops.loc[:, "stop_sequence"] = self.normalize_sequence(stops["seq_id"])

        return stops

    # ----------------------------------------------------------------------
    # Geometry functions

    @staticmethod
    def geoms_from_shapes(lines: pd.DataFrame, shapes: pd.DataFrame) -> pd.DataFrame:
        """Return the arc geometries from shapes, for arcs with shape_id and shape_distances"""

        df = pd.merge(
            lines,
            shapes.drop(columns="geometry"),
            left_on="shape_id",
            right_index=True,
            how="left",
        )

        _from_pos = df["shape_dist"].listarray.search_sorted(
            df["from_dist"], inclusive=True
        )
        _to_pos = df["shape_dist"].listarray.search_sorted(
            df["to_dist"], inclusive=True
        )

        sliced_lon = df["lon"].listarray.slice(start=_from_pos, end=_to_pos)
        sliced_lat = df["lat"].listarray.slice(start=_from_pos, end=_to_pos)

        # interpolate first point
        ratio = df["shape_dist"].listarray.interpolation_ratio(
            _from_pos, df["from_dist"]
        )
        ratio.loc[_from_pos == 0] = 0

        lon = df["lon"].listarray.interpolate(_from_pos, ratio)
        sliced_lon = sliced_lon.listarray.insert(lon, 0)

        lat = df["lat"].listarray.interpolate(_from_pos, ratio)
        sliced_lat = sliced_lat.listarray.insert(lat, 0)

        # interpolate last point
        ratio = df["shape_dist"].listarray.interpolation_ratio(_to_pos, df["to_dist"])
        ratio = ratio.fillna(0)
        positions = sliced_lon.list.len()

        lon = df["lon"].listarray.interpolate(_to_pos, ratio)
        sliced_lon = sliced_lon.listarray.insert(lon, positions)

        lat = df["lat"].listarray.interpolate(_to_pos, ratio)
        sliced_lat = sliced_lat.listarray.insert(lat, positions)

        geoms = Linestrings(sliced_lon, sliced_lat, crs=4326)
        geoms.index = lines.index

        return geoms.remove_repeated_points()

    def add_geometries(self, arcs, shapes):
        """Add geometries to arcs"""

        # 2 methods : use shapes or connect geometry and to_geometry
        mask = (arcs.shape_id.isna()) | (arcs.from_dist.isna()) | (arcs.to_dist.isna())
        arcs = arcs.rename(columns={"geometry": "geom"})

        # add geometries with shape_id
        df = arcs.loc[~mask, ["from_dist", "to_dist", "shape_id", "line_gid"]]
        if len(df) > 0:
            geoms = self.geoms_from_shapes(df, shapes)
            arcs.loc[~mask, "geometry"] = geoms
            arcs = arcs.set_geometry("geometry", crs=4326)

        # add geometries without shape_ids or shapes dataframe
        if len(arcs.loc[mask]) > 0:
            # if previous arc has geometry, use it as from point, else use from_stop geometry
            if "geometry" in arcs.columns:
                prev_mask = (
                    (mask)
                    & (~arcs["geometry"].shift(1).isna())
                    & (arcs["line_gid"].shift(1) == arcs["line_gid"])
                )
                prev_point = last_point(arcs["geometry"].shift(1).loc[prev_mask])
                arcs.loc[prev_mask, "geom"] = prev_point
            pt1 = gpd.GeoSeries(arcs.loc[mask, "geom"], crs=4326)

            # if next arc has geometry, use it as to point, else use from_stop geometry
            if "geometry" in arcs.columns:
                next_mask = (
                    (mask)
                    & (arcs["geometry"].shift(-1).notna())
                    & (arcs["line_gid"].shift(-1) == arcs["line_gid"])
                )
                next_point = first_point(arcs["geometry"].shift(-1).loc[next_mask])
                arcs.loc[next_mask, "to_geom"] = next_point
            pt2 = gpd.GeoSeries(arcs.loc[mask, "to_geom"], crs=4326)

            arcs.loc[mask, "geometry"] = connect_points(pt1, pt2)

        arcs = arcs.drop(
            columns=["shape_id", "from_dist", "to_dist", "geom", "to_geom"]
        )
        arcs = arcs.set_geometry("geometry", crs=4326)
        return arcs

    def stops_on_shapes(
        self, lines: pd.DataFrame, shapes: pd.DataFrame
    ) -> pd.DataFrame:
        """return the missing shape_dist on the shape geometry"""

        line_mask = (lines.shape_id.notna()) & (lines.shape_dist.isna())
        df = lines.loc[line_mask, ["stop_gid", "shape_id", "geometry"]]

        if len(df) == 0:
            return lines["shape_dist"]

        df = df.drop_duplicates()
        df = df.set_geometry("geometry").to_crs(self.crs)

        df = pd.merge(
            df,
            shapes.to_crs(self.crs)["geometry"].to_frame("shape_geometry"),
            left_on="shape_id",
            right_index=True,
            how="left",
        )

        # find projection distance
        df["shape_dist"] = df.shape_geometry.project(df.geometry)

        results = lines.loc[line_mask, ["stop_gid", "shape_id"]]
        results.index.name = "_ix"

        results = pd.merge(
            results.reset_index(),
            df[["stop_gid", "shape_id", "shape_dist"]],
            on=["stop_gid", "shape_id"],
            how="left",
        )
        return results.set_index("_ix")["shape_dist"]

    # ----------------------------------------------------------------------
    # Arc / Lines building functions

    @staticmethod
    def is_on_demand(lines):
        pickup = (lines.continuous_pickup.isna()) | (lines.continuous_pickup == 1)
        drop_off = (lines.continuous_drop_off.isna()) | (lines.continuous_drop_off == 1)
        return ~pickup | ~drop_off

    @staticmethod
    def unique_headsigns(stops: pd.DataFrame) -> pd.DataFrame:
        """Unify trip_headsign and stop_headsign to headsign"""

        if "trip_headsign" in stops.columns and "stop_headsign" in stops.columns:
            stops["headsign"] = stops["trip_headsign"].fillna(stops["stop_headsign"])
            stops = stops.drop(columns=["trip_headsign", "stop_headsign"])
        elif "trip_headsign" in stops.columns:
            stops = stops.rename(columns={"trip_headsign": "headsign"})
        elif "stop_headsign" in stops.columns:
            stops = stops.rename(columns={"stop_headsign": "headsign"})

        return stops

    @staticmethod
    def unique_on_demand(stops=pd.DataFrame) -> pd.DataFrame:
        """unify drop_off and pickup columns from routes and stoptimes"""
        cols = stops.columns
        if "routes_drop_off" not in cols and "routes_pickup" not in cols:
            return stops

        if "routes_drop_off" in cols:
            if "continuous_drop_off" in cols:
                stops.loc[stops.continuous_drop_off.isna(), "continous_drop_off"] = (
                    stops["routes_drop_off"]
                )
            else:
                stops["continuous_drop_off"] = stops["routes_drop_off"]
            del stops["routes_drop_off"]

        if "routes_pickup" in cols:
            if "continuous_pickup" in cols:
                stops.loc[stops.continuous_pickup.isna(), "continous_pickup"] = stops[
                    "routes_pickup"
                ]
            else:
                stops["continuous_pickup"] = stops["routes_pickup"]
            del stops["routes_pickup"]

        return stops

    @staticmethod
    def unique_stops(lines, stops):
        """Make stop_gid unique for each agency_gid and add agency_gid to stops"""

        dup = lines[["stop_id", "agency_gid"]].drop_duplicates()
        df = pd.merge(stops, dup, on="stop_id", how="left").reset_index(drop=True)

        # create unique stop_gid per agency_gid + stop_id
        df.loc[:, "stop_gid"] = random_ids(len(df))
        df["stop_gid"] = df["stop_gid"].astype("uint64[pyarrow]")
        return df.set_index("stop_gid", drop=True)

    @staticmethod
    def expand_days_and_compress(lines):
        """Compress lines by line_gid, results are sorted by line_gid and stop_sequence"""

        df = lines.sort_values(["line_gid", "stop_sequence"], ignore_index=True)

        merged = df.drop(columns=["departure", "arrival", "time"]).copy()
        merged = merged.drop_duplicates(["line_gid", "stop_sequence"], keep="first")
        merged = merged.reset_index(drop=True)

        cols = ["line_gid", "stop_sequence", "departure", "arrival", "time"]
        exploded = df[cols].explode("time")
        exploded["departure"] += exploded["time"]
        exploded["arrival"] += exploded["time"]
        del exploded["time"]

        cols = ["line_gid", "stop_sequence"]
        merged.loc[:, "departure"] = ls.list_array(
            exploded["departure"], exploded[cols], ignore_index=True
        )
        merged.loc[:, "arrival"] = ls.list_array(
            exploded["arrival"], exploded[cols], ignore_index=True
        )

        return merged

    @staticmethod
    def to_arcs(lines):
        """Convert to arcs format"""

        # drop unnecessary columns, rename to arcs format
        df = lines.rename(columns={"stop_gid": "from_stop", "shape_dist": "from_dist"})

        cols = [
            "to_stop",
            "to_dist",
            "n_arrival",
            "to_geom",
            "n_stop_seq",
            "n_ligne_gid",
        ]
        df[cols] = df[
            [
                "from_stop",
                "from_dist",
                "arrival",
                "geometry",
                "stop_sequence",
                "line_gid",
            ]
        ].shift(-1, axis=0)

        # drop last arc in line : when line_gid changes
        df = df.loc[df.line_gid == df.n_ligne_gid].copy()

        # last stop_sequence to LAST_STOP
        df.loc[df["n_stop_seq"] == LAST_STOP, "stop_sequence"] = LAST_STOP

        # travel time on arc
        df["travel"] = df["n_arrival"].listarray.subtract(df["departure"])

        df = df.drop(columns=["n_ligne_gid", "n_arrival", "n_stop_seq", "arrival"])

        return df

    def parse_lines(self, parser: GTFSParser) -> pd.DataFrame:
        """Make a Trips dataframe, for a week/year"""

        # ------------------------------------------------
        # Prepare trips, add services and routes

        trips = self.parse_trips(parser)

        # ------------------------------------------------
        # parse stoptimes
        df = self.parse_stoptimes(parser, trips["trip_id"])

        # combine with trips
        df = df.merge(trips, how="inner", on="trip_id").drop(columns="trip_id")

        # add data from stops
        self.stops = self.unique_stops(df, self.stops)
        cols = ["stop_id", "agency_gid", "geometry", "stop_name"]
        df = df.merge(self.stops[cols].reset_index(), on=["stop_id", "agency_gid"])
        del df["stop_id"]

        # unify data from trips, routes and stops
        df = self.unique_headsigns(df)

        # add on_demand boolean
        df = self.unique_on_demand(df)
        df["on_demand"] = self.is_on_demand(df)

        # ------------------------------------------------
        # create shapes

        shapes = self.parse_shapes(
            parser, df["shape_id"] if "shape_id" in df.columns else None
        )

        # update stop_times with missing shape_dist from shapes
        if shapes is not None:
            df, shapes = intersect_ids(df, shapes, left_id="shape_id", drop_left=False)
            projected = self.stops_on_shapes(df, shapes)
            df.loc[projected.index, "shape_dist"] = projected
            df = self.correct_short_shapedist(shapes, df)

            mask = self.is_invalid_dist(df["shape_dist"], df["stop_sequence"])
            df.loc[mask, "shape_dist"] = pd.NA
        else:
            df["shape_dist"] = pd.NA
            df["shape_id"] = pd.NA

        # ------------------------------------------------
        # create lines / arcs
        # add line index
        line_ids = self.get_line_id(df, self.name).to_frame("line_gid")
        df = pd.merge(df, line_ids, left_on="seq_id", right_index=True)
        del df["seq_id"]

        df = self.expand_days_and_compress(df)
        df = self.to_arcs(df)
        df = self.add_geometries(df, shapes)

        return df
