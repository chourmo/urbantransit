import io
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from pyarrow import csv

from .agency import GTFSAgencyParser
from .calendar import GTFSCalendarParser
from .calendar_dates import GTFSCalendarDatesParser
from .feed_info import GTFSFeedInfoParser
from .levels import GTFSLevelsParser
from .parser_schema import REQUIRED_FILES
from .routes import GTFSRoutesParser
from .shapes import GTFSShapesParser
from .stop_times import GTFSStopTimesParser
from .stops import GTFSStopsParser
from .transfers import GTFSTransfersParser
from .trips import GTFSTripsParser


class GTFSParser:
    """Load and validate a GTFS feed from a zip archive or directory.

    This class checks that the supplied GTFS input contains the required files,
    exposes file-specific parser objects for the individual GTFS datasets, and
    provides helper methods for summary statistics and lightweight repairs.

    Parameters
    ----------
    path : Path
        Path to a GTFS archive (.zip) or to an extracted GTFS directory.
    fix_inner_folder : bool, default=False
        If True, normalize zipped feeds that contain GTFS files under an extra
        inner folder.

    Raises
    ------
    ValueError
        If the input is not a GTFS directory or zip file, or if the feed is
        missing required files.

    Notes
    -----
    A valid GTFS feed must include at least the required files for routing and
    validation, namely agency.txt, stops.txt, routes.txt, trips.txt,
    stop_times.txt, and either calendar.txt or calendar_dates.txt.
    """

    required_files = REQUIRED_FILES

    def __init__(self, path: Path | str, fix_inner_folder=False):
        """init from a path to a zip file or a folder"""

        self.path = Path(path)
        self.base_name = self.path.stem

        if not self.is_dir() and not self.is_zip():
            raise ValueError(f"{self.path} is neither a zip file or a directory")

        if fix_inner_folder and self.has_inner_folder():
            self.fix_dir_in_zip()

        # cache filenames
        self.files = self._filenames()

        if not self.has_required_files():
            raise ValueError(f"{self.path} is missing required files")

        # cache of parser objects returned by the get_ methods, keyed by parser class
        self._parser_cache: dict[type, Any] = {}

    # validate path is a GTFS dir or zip file
    def is_dir(self):
        return self.path.is_dir()

    def is_zip(self):
        return self.path.is_file() and zipfile.is_zipfile(self.path)

    def _filenames(self):
        if self.is_dir():
            return {f.name for f in self.path.iterdir() if f.is_file()}
        if self.is_zip():
            with zipfile.ZipFile(self.path, "r") as archive:
                files = {x for x in archive.namelist()}
            return files

    def has_required_files(self):
        """Verify if all required files exist"""

        # test if all required files are in file
        if not self.files.issuperset(set(self.required_files)):
            return False

        # test if either calendar.txt or calendar_dates.txt exist
        return "calendar.txt" in self.files or "calendar_dates.txt" in self.files

    def validate(self) -> bool:
        """Validate every supported GTFS file in the feed.

        Each parser validates its file while loading it, including required
        columns and unique identifiers. Optional files are also loaded when
        present. Parsing or validation errors are raised to the caller.
        """

        parser_classes = (
            GTFSAgencyParser,
            GTFSRoutesParser,
            GTFSStopsParser,
            GTFSTripsParser,
            GTFSStopTimesParser,
            GTFSCalendarParser,
            GTFSCalendarDatesParser,
            GTFSShapesParser,
            GTFSFeedInfoParser,
            GTFSTransfersParser,
            GTFSLevelsParser,
        )

        for parser_class in parser_classes:
            self._get_parser(parser_class)

        required_fields = {
            GTFSAgencyParser: ("agency_name", "agency_url", "agency_timezone"),
            GTFSRoutesParser: ("route_id", "route_type"),
            GTFSStopsParser: ("stop_id", "stop_name"),
            GTFSTripsParser: ("route_id", "service_id", "trip_id"),
            GTFSStopTimesParser: ("trip_id", "stop_id", "stop_sequence"),
            GTFSCalendarParser: (
                "service_id",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "start_date",
                "end_date",
            ),
            GTFSCalendarDatesParser: ("service_id", "date", "exception_type"),
            GTFSShapesParser: (
                "shape_id",
                "shape_pt_lat",
                "shape_pt_lon",
                "shape_pt_sequence",
            ),
            GTFSFeedInfoParser: (
                "feed_publisher_name",
                "feed_publisher_url",
                "feed_lang",
            ),
            GTFSTransfersParser: ("from_stop_id", "to_stop_id", "transfer_type"),
            GTFSLevelsParser: ("level_id", "level_index"),
        }

        for parser_class, fields in required_fields.items():
            if parser_class.filename not in self.files:
                continue
            data = self._parser_cache[parser_class].data
            missing = [field for field in fields if data[field].isna().any()]
            if missing:
                raise ValueError(
                    f"{', '.join(missing)} contains null values in "
                    f"{parser_class.filename}"
                )

        routes = self._parser_cache[GTFSRoutesParser].data
        if not routes.empty and routes[["route_short_name", "route_long_name"]].isna().all(axis=1).any():
            raise ValueError(
                "at least one of route_short_name or route_long_name is required "
                "in routes.txt"
            )

        stop_times = self._parser_cache[GTFSStopTimesParser].data
        if not stop_times.empty and stop_times[["arrival_time", "departure_time"]].isna().all(axis=1).any():
            raise ValueError(
                "at least one of arrival_time or departure_time is required "
                "in stop_times.txt"
            )

        stops = self._parser_cache[GTFSStopsParser].data
        if not stops.empty:
            location_type = stops["location_type"].fillna(0)
            missing_coordinates = (
                (location_type == 0)
                & (stops[["stop_lat", "stop_lon"]].isna().any(axis=1))
            )
            if missing_coordinates.any():
                raise ValueError(
                    "stop_lat and stop_lon are required for stops with "
                    "location_type 0 in stops.txt"
                )

        self._validate_enum(GTFSRoutesParser, "route_type", range(12))
        self._validate_enum(GTFSStopsParser, "location_type", range(6))
        self._validate_enum(GTFSStopsParser, "wheelchair_boarding", range(3))
        self._validate_enum(GTFSTripsParser, "direction_id", range(2))
        self._validate_enum(GTFSTripsParser, "wheelchair_accessible", range(3))
        self._validate_enum(GTFSTripsParser, "bikes_allowed", range(3))
        self._validate_enum(GTFSTripsParser, "cars_allowed", range(3))
        self._validate_enum(GTFSStopTimesParser, "pickup_type", range(4))
        self._validate_enum(GTFSStopTimesParser, "drop_off_type", range(4))
        self._validate_enum(GTFSCalendarDatesParser, "exception_type", (1, 2))
        self._validate_enum(GTFSTransfersParser, "transfer_type", range(4))

        self._validate_range(GTFSStopsParser, "stop_lat", -90, 90)
        self._validate_range(GTFSStopsParser, "stop_lon", -180, 180)
        self._validate_range(GTFSShapesParser, "shape_pt_lat", -90, 90)
        self._validate_range(GTFSShapesParser, "shape_pt_lon", -180, 180)
        self._validate_range(GTFSShapesParser, "shape_pt_sequence", 0, None)
        self._validate_range(GTFSStopTimesParser, "stop_sequence", 0, None)
        self._validate_range(GTFSStopTimesParser, "shape_dist_traveled", 0, None)
        self._validate_range(GTFSTransfersParser, "min_transfer_time", 0, None)

        calendar = self._parser_cache[GTFSCalendarParser].data
        if not calendar.empty and (calendar["start_date"] > calendar["end_date"]).any():
            raise ValueError("start_date must not be after end_date in calendar.txt")

        time_pattern = r"^(?:[0-9]+):[0-5][0-9]:[0-5][0-9]$"
        for field in ("arrival_time", "departure_time"):
            values = stop_times[field].dropna().astype("string")
            if not values.str.fullmatch(time_pattern).all():
                raise ValueError(f"invalid {field} values in stop_times.txt")

        return True

    def validate_ids(self) -> bool:
        """Validate the feed and its cross-file identifiers.

        This runs :meth:`validate` first, then checks that every populated
        foreign-key column refers to an identifier in the corresponding file.
        """

        self.validate()

        agency = self._parser_cache[GTFSAgencyParser].data
        routes = self._parser_cache[GTFSRoutesParser].data
        stops = self._parser_cache[GTFSStopsParser].data
        trips = self._parser_cache[GTFSTripsParser].data
        stop_times = self._parser_cache[GTFSStopTimesParser].data
        shapes = self._parser_cache[GTFSShapesParser].data
        levels = self._parser_cache[GTFSLevelsParser].data
        transfers = self._parser_cache[GTFSTransfersParser].data
        calendar = self._parser_cache[GTFSCalendarParser].data
        calendar_dates = self._parser_cache[GTFSCalendarDatesParser].data

        self._validate_reference(
            routes,
            "agency_id",
            agency,
            "agency_id",
            "routes.txt",
        )
        self._validate_reference(
            trips,
            "route_id",
            routes,
            "route_id",
            "trips.txt",
        )
        self._validate_reference(
            trips,
            "service_id",
            pd.concat(
                [calendar[["service_id"]], calendar_dates[["service_id"]]],
                ignore_index=True,
            ),
            "service_id",
            "trips.txt",
        )
        self._validate_reference(
            trips,
            "shape_id",
            shapes,
            "shape_id",
            "trips.txt",
        )
        self._validate_reference(
            stop_times,
            "trip_id",
            trips,
            "trip_id",
            "stop_times.txt",
        )
        self._validate_reference(
            stop_times,
            "stop_id",
            stops,
            "stop_id",
            "stop_times.txt",
        )
        self._validate_reference(
            stops,
            "parent_station",
            stops,
            "stop_id",
            "stops.txt",
        )
        self._validate_reference(
            stops,
            "level_id",
            levels,
            "level_id",
            "stops.txt",
        )
        self._validate_reference(
            transfers,
            "from_stop_id",
            stops,
            "stop_id",
            "transfers.txt",
        )
        self._validate_reference(
            transfers,
            "to_stop_id",
            stops,
            "stop_id",
            "transfers.txt",
        )
        self._validate_reference(
            transfers,
            "from_route_id",
            routes,
            "route_id",
            "transfers.txt",
        )
        self._validate_reference(
            transfers,
            "to_route_id",
            routes,
            "route_id",
            "transfers.txt",
        )
        self._validate_reference(
            transfers,
            "from_trip_id",
            trips,
            "trip_id",
            "transfers.txt",
        )
        self._validate_reference(
            transfers,
            "to_trip_id",
            trips,
            "trip_id",
            "transfers.txt",
        )

        return True

    @staticmethod
    def _validate_reference(
        source: pd.DataFrame,
        source_field: str,
        target: pd.DataFrame,
        target_field: str,
        source_filename: str,
    ) -> None:
        values = source[source_field].dropna()
        if values.empty:
            return

        target_values = target[target_field].dropna()
        missing = values[~values.isin(target_values)].drop_duplicates()
        if not missing.empty:
            identifiers = ", ".join(str(value) for value in missing.tolist())
            raise ValueError(
                f"{source_filename}.{source_field} contains unknown identifiers: "
                f"{identifiers}"
            )

    def _validate_enum(self, parser_class, field: str, values) -> None:
        if parser_class.filename not in self.files:
            return
        data = self._parser_cache[parser_class].data[field].dropna()
        if not data.isin(values).all():
            raise ValueError(f"invalid {field} values in {parser_class.filename}")

    def _validate_range(
        self, parser_class, field: str, minimum: float, maximum: float | None
    ) -> None:
        if parser_class.filename not in self.files:
            return
        data = self._parser_cache[parser_class].data[field].dropna()
        invalid = data < minimum
        if maximum is not None:
            invalid = invalid | (data > maximum)
        if invalid.any():
            raise ValueError(f"invalid {field} values in {parser_class.filename}")

    def calendar_statistics(self):
        """return a dataframe with number of unique days in week and number of trips for each week and year"""
        calendars = self.merged_calendars()
        trips = self.get_trips().data[["service_id", "trip_id"]]

        stats = pd.merge(calendars, trips, on="service_id", how="left")
        stats = stats.groupby(["year", "week"]).agg(
            {"date": "nunique", "trip_id": "nunique"}
        )
        stats = stats.rename(columns={"date": "days_of_week", "trip_id": "trips"})

        return stats

    # ----------------------------------------------------------------------------------
    # get files

    def get_agency(self):
        """Returns agency object"""
        return self._get_parser(GTFSAgencyParser)

    def get_routes(self):
        """Returns routes object"""
        return self._get_parser(GTFSRoutesParser)

    def get_stops(self):
        """Returns stops object"""
        return self._get_parser(GTFSStopsParser)

    def get_trips(self):
        """Returns trips object"""
        return self._get_parser(GTFSTripsParser)

    def get_stoptimes(self, trips=None):
        """Returns stop_times object"""
        return self._get_parser(GTFSStopTimesParser)

    def get_calendar(self):
        """Returns calendar object"""
        return self._get_parser(GTFSCalendarParser)

    def get_calendardates(self):
        """Returns calendar_dates object"""
        return self._get_parser(GTFSCalendarDatesParser)

    def get_shapes(self):
        """Returns shapes object"""
        return self._get_parser(GTFSShapesParser)

    def get_feedinfo(self):
        """Returns feed_info object"""
        return self._get_parser(GTFSFeedInfoParser)

    def get_transfer(self):
        """Returns transfer object"""
        return self._get_parser(GTFSTransfersParser)

    def get_levels(self):
        """Returns levels object"""
        return self._get_parser(GTFSLevelsParser)

    def merged_calendars(self):
        """Returns a dataframe of valid days based on calendar and calendar_dates"""

        cal = self.get_calendar()
        cal = cal.expand()
        cal_dates = self.get_calendardates().data

        # merge cal and cal_dates
        if cal_dates is not None and cal is not None:
            cal = pd.merge(cal, cal_dates, on=["service_id", "date"], how="outer")

            # drop if exception_type is 2
            cal = cal.loc[(cal.exception_type == 1) | (cal.exception_type.isna())]
            del cal["exception_type"]

        elif cal is None:
            cal = cal_dates.loc[cal_dates.exception_type == 1].copy()
            del cal["exception_type"]

        cal = cal.reset_index(drop=True)
        isocal = cal["date"].dt.isocalendar()
        cal["week"] = isocal.week
        cal["year"] = isocal.year

        return cal

    def _get_parser(self, file_class):
        """Build (or return the cached) parser instance for a given parser class"""

        if file_class in self._parser_cache:
            return self._parser_cache[file_class]

        name = file_class.filename
        spec = file_class.spec

        if name not in self.files:
            parser = file_class(None)
        else:
            if file_class.file_type == "csv":
                df = self._read_csv(name, spec)

            # TODO implement geojson parsing of locations

            parser = file_class(df, base_name=self.base_name)

        self._parser_cache[file_class] = parser
        return parser

    def _read_csv(self, name: str, spec: dict[str, Any]) -> pd.DataFrame:
        """read csv with pyarrow engine, return None if name does not exist"""

        # use pyarrow csv parser to read columns even if missing

        convert = csv.ConvertOptions(
            column_types=spec,
            include_columns=list(spec.keys()),
            include_missing_columns=True,
            strings_can_be_null=True,
            timestamp_parsers=["%Y%m%d"],
        )
        parse = csv.ParseOptions(delimiter=",")

        if self.is_zip():
            with zipfile.ZipFile(self.path, "r") as archive:
                path = next(
                    x
                    for x in archive.namelist()
                    if zipfile.Path(archive, x).name == name
                )
                with archive.open(path) as f:
                    array = csv.read_csv(
                        f, convert_options=convert, parse_options=parse
                    )
        else:
            path = self.path / name
            array = csv.read_csv(path, convert_options=convert, parse_options=parse)

        return array.to_pandas(types_mapper=pd.ArrowDtype)

    def update(self, mapper: dict[str, Any]):
        """Update file in zip or directory based on on a dict of filename : new dataframe"""

        if self.is_dir():
            for filename, parser in mapper.items():
                parser.to_file(self.path / filename)

        # extract files from zip in temporary folder
        temp_path = self.path.with_stem(self.path.stem + "_temp")

        with (
            zipfile.ZipFile(self.path, "r") as archive,
            zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_BZIP2, compresslevel=8
            ) as new_archive,
        ):
            # save new files
            for filename, parser in mapper.items():
                stream = io.StringIO()
                parser.to_file(stream)
                new_archive.writestr(
                    zipfile.Path(archive, filename).name, stream.getvalue()
                )

            # copy unmodified files
            for file in [x for x in archive.namelist() if x not in mapper]:
                new_archive.writestr(
                    zipfile.Path(archive, file).name, archive.open(file).read()
                )

        self.path = temp_path.replace(self.path)

        # invalidate cached parsers, since underlying files have changed
        self._parser_cache.clear()

    # ----------------------------------------------------------------------------------
    # global test and fix functions

    def has_inverted_stopcodes(self):
        """test if stop_codes and stop_ids are mismatched"""

        stops = self.get_stops().data

        # simple case : stop_code not in dataframe or filled with nulls
        if "stop_code" not in stops.columns or stops["stop_code"].isnull().all():
            return False

        df = self.get_stoptimes().data
        df = df["stop_id"].drop_duplicates()

        match_ids = len(df.loc[df.isin(stops["stop_id"])])
        match_codes = len(df.loc[df.isin(stops["stop_code"])])

        return match_ids < match_codes

    def has_inner_folder(self):
        """test if there is an inner directory in zip file"""

        if not self.is_zip():
            return False

        with zipfile.ZipFile(self.path, "r") as archive:
            files = [zipfile.Path(archive, x).is_dir() for x in archive.namelist()]
        return any(files)

    def fix_dir_in_zip(self):
        if not self.is_zip() or not self.has_inner_folder():
            return None  # noqa: RET501

        # extract files from zip in temporary folder
        temp_path = self.path.with_stem(self.path.stem + "_temp")

        with (
            zipfile.ZipFile(self.path, "r") as archive,
            zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_BZIP2, compresslevel=8
            ) as new_archive,
        ):
            for file in [
                x for x in archive.namelist() if not zipfile.Path(archive, x).is_dir()
            ]:
                new_archive.writestr(
                    zipfile.Path(archive, file).name, archive.open(file).read()
                )

        self.path = temp_path.replace(self.path)
