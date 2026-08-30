import io
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
from geometryhelpers import geographic_distance
from pyarrow import csv

from .utils.logging import transitlog


class GTFSParser:
    """GTFS zip or folder parser and validation class"""

    required_files = [
        "agency.txt",
        "stops.txt",
        "routes.txt",
        "trips.txt",
        "stop_times.txt",
    ]

    def __init__(self, path: Path, fix_inner_folder=False):
        """init from a path to a zip file or a folder"""

        self.path = path
        self.base_name = path.stem

        if not self.is_dir() and not self.is_zip():
            raise ValueError(f"{path} is neither a zip file or a directory")

        if fix_inner_folder and self.has_inner_folder():
            self.fix_dir_in_zip()

        # cache filenames
        self.files = self._filenames()

        if not self.has_required_files():
            raise ValueError(f"{path} is missing required files")

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

    def calendar_statistics(self):
        """return a dataframe of week, year and number of trips"""
        calendars = self.merged_calendars()
        trips = self.get_trips().data

        calendars = calendars.groupby(["week", "year", "service_id"]).size()
        calendars = calendars.to_frame("size").reset_index()

        df = pd.merge(trips, calendars, on="service_id", how="left")
        df = df.groupby(["year", "week"], sort="ascending")["size"].sum()

        return df

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
        name = file_class.filename
        spec = file_class.spec

        if name not in self.files:
            return file_class(None)

        if file_class.file_type == "csv":
            df = self._read_csv(name, spec)

        # TODO implement geojson parsing of locations

        return file_class(df, base_name=self.base_name)

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

    def update(self, mapper: dict[str:Any]):
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


# ----------------------------------------------------------------------------------
# parse individual files


class GTFSFileParser:
    """Base class for GTFS parsers"""

    def __init__(
        self,
        df: pd.DataFrame | None,
        filename: str,
        spec: dict[str, Any],
        defaults: dict[str, Any] | None = None,
        boolean_cols: dict[str, tuple[Any, Any, Any]] | None = None,
        is_required: bool = True,
        unique_id: str | None = None,
        file_type="csv",
        base_name: str | None = None,
    ):
        """Init from a dataframe, drop columns not in spec or defaults
            - if is_required is True and df is None, raise an error
            - if not required and df is None, return an empty dataframe with spec columns and defaults values
            - check unicity of unique_id column if specified,
            - map boolean columns if specified,
            - set defaults values for missing columns

        Arguments:
            df : dataframe
            filename : GTFS name of a file, without .txt
            spec : dictionary of column name : Arrow Type
            defaults : optional defaults values column name : optional value
            boolean_cols : optional dict of columns names to replace by optional booleans (True Value, False Value)
            is_required : file must exist if True
            unique_id : optional column name that must exist and be unique in file
            file_type : str of file_type
            base_name : optional name of the base file

        """

        if df is None:
            if is_required:
                raise ValueError(f"{filename} is required but missing")
            else:
                self.empty = True
                df = self.empty_frame()
        else:
            self.empty = False

        # check unicity of unique_ids
        if unique_id is not None:
            if not unique_id not in df.columns:
                raise ValueError(f"{unique_id} is missing in {filename}")
            if not df[unique_id].is_unique:
                raise ValueError(f"{unique_id} is not unique in {filename}")

        # parse booleans
        if boolean_cols is not None:
            cols = df.columns
            for col, (is_true, is_false, is_empty) in (
                x for x in boolean_cols.items() if x[0] in cols
            ):
                df[col] = self.to_boolean(df[col], is_true, is_false, is_empty)

        # drop columns not in spec and without defaults
        df = self.set_defaults(df, spec, defaults)
        self.data = df

    def empty_frame(self):
        """return an empty dataframe with spec columns and defaults values"""

        schema = pa.schema(self.spec)
        return schema.empty_table().to_pandas(types_mapper=pd.ArrowDtype)

    def set_defaults(
        self, df: pd.DataFrame, spec: dict[str, Any], defaults: dict[str, Any] | None
    ) -> pd.DataFrame:
        """Set options for the DataFrame based on options and dtypes"""

        res = df.copy()
        if defaults is not None:
            res = res.loc[:, res.columns.isin(spec.keys() | defaults.keys())]
        else:
            return res.loc[:, res.columns.isin(spec.keys())]

        new_cols = [col for col in defaults if col not in res.columns]

        for col in new_cols:
            res[col] = pd.Series(data=defaults[col], dtype=spec[col][1])

        return res

    @staticmethod
    def to_boolean(df: pd.Series, trueValue: Any, falseValue: Any, emptyValue: Any):
        """Map df to nullable boolean"""
        res = df.map({True: trueValue, False: falseValue})
        res.loc[df == emptyValue] = pd.NA
        return res.astype(pd.ArrowDtype(pa.bool_()))

    @staticmethod
    def bool_to_value(
        df: pd.Series, dtype, trueValue: Any, falseValue: Any, emptyValue: Any
    ) -> pd.Series:
        """convert a boolean Series to a series of dtype based on trueValue and emptyValue"""

        res = df.map({trueValue: True, falseValue: False}).astype(dtype)
        res.loc[df == emptyValue] = pd.NA

        return res

    def to_file(self, path_or_buf, sep=","):
        """save to csv to path of buffer, convert boolean columns to values"""

        df = self.data

        # map booleans to int if not in boolean_cols (simple case)
        for col in [x for x in df.columns if pd.api.types.is_bool_dtype(df[x])]:
            df[col] = df[col].astype(pd.ArrowDtype(pa.uint8()))

        # map booleans for cols in boolean_cols
        if self.boolean_cols is not None:
            for col, (trueValue, falseValue, emptyValue) in self.boolean_cols.items():
                dtype = pd.ArrowDtype(self.spec[col])
                df[col] = self.bool_to_value(
                    df[col], dtype, trueValue, falseValue, emptyValue
                )

        if self.file_type == "csv":
            df.to_csv(path_or_buf, sep=",", index=False)


class GTFSAgencyParser(GTFSFileParser):
    """Class to parse GTFS Agency.txt file"""

    filename: str = "agency.txt"
    is_required: bool = True
    unique_id: str | None = "agency_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "agency_id": pa.string(),
        "agency_name": pa.string(),
        "agency_url": pa.string(),
        "agency_timezone": pa.string(),
        "agency_lang": pa.string(),
        "agency_phone": pa.string(),
        "agency_fare_url": pa.string(),
        "agency_email": pa.string(),
    }
    defaults: dict[str, Any] = {"agency_id": 1}  # Default agency_id is missings
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )

        self.base_name = base_name


class GTFSRoutesParser(GTFSFileParser):
    """Class to parse GTFS Routes.txt file"""

    filename: str = "routes.txt"
    is_required: bool = True
    unique_id: str | None = "route_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "route_id": pa.string(),
        "agency_id": pa.string(),
        "route_short_name": pa.string(),
        "route_long_name": pa.string(),
        "route_desc": pa.string(),
        "route_type": pa.uint8(),
        "route_url": pa.string(),
        "route_color": pa.string(),
        "route_text_color": pa.string(),
        "route_sort_order": pa.uint8(),
        "continuous_pickup": pa.uint8(),
        "continuous_drop_off": pa.uint8(),
    }
    defaults: dict[str, Any] = {
        "agency_id": 1,  # Default agency_id is missing
        "continuous_pickup": 0,
        "continuous_drop_off": 0,
        "route_sort_order": 0,
    }
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )

        self.base_name = base_name


class GTFSStopsParser(GTFSFileParser):
    """Class to parse GTFS Stops.txt file"""

    filename: str = "stops.txt"
    is_required: bool = True
    unique_id: str | None = "stop_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "stop_id": pa.string(),
        "stop_code": pa.string(),
        "stop_name": pa.string(),
        "stop_desc": pa.string(),
        "stop_lat": pa.float64(),
        "stop_lon": pa.float64(),
        "zone_id": pa.string(),
        "stop_url": pa.string(),
        "location_type": pa.uint8(),
        "parent_station": pa.string(),
        "stop_timezone": pa.string(),
        "wheelchair_boarding": pa.uint8(),
        "level_id": pa.string(),
        "platform_code": pa.string(),
    }
    defaults: dict[str, Any] = {
        "location_type": 0,
        "wheelchair_boarding": 0,
    }
    boolean_cols: dict[str, tuple[Any, Any]] | None = {"wheelchair_boarding": (1, 2, 0)}

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name

    def fix_codes(self) -> None:
        """Fix stop_code and stop_id columns if they are mixed"""

        df = self.data

        if "stop_code" not in df.columns:
            return None  # noqa: RET501

        # swap stop_code and stop_id
        df = df.rename(columns={"stop_code": "stop_id", "stop_id": "stop_code"})

        self.data = df


class GTFSTripsParser(GTFSFileParser):
    """Class to parse GTFS Trips.txt file"""

    filename: str = "trips.txt"
    is_required: bool = True
    unique_id: str | None = "trip_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "route_id": pa.string(),
        "service_id": pa.string(),
        "trip_id": pa.string(),
        "trip_headsign": pa.string(),
        "trip_short_name": pa.string(),
        "direction_id": pa.uint8(),
        "block_id": pa.string(),
        "shape_id": pa.string(),
        "wheelchair_accessible": pa.uint8(),
        "bikes_allowed": pa.uint8(),
        "cars_allowed": pa.uint8(),
    }
    defaults: dict[str, Any] = {
        "trip_headsign": None,
        "trip_short_name": None,
        "direction_id": None,
        "block_id": None,
        "shape_id": None,
        "wheelchair_accessible": 0,
        "bikes_allowed": 0,
    }
    boolean_cols: dict[str, tuple[Any, Any]] | None = {
        "bikes_allowed": (1, 2, 0),
        "wheelchair_accessible": (1, 2, 0),
        "cars_allowed": (1, 2, 0),
    }

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name


class GTFSStopTimesParser(GTFSFileParser):
    """Class to parse GTFS StopTimes.txt file"""

    filename: str = "stop_times.txt"
    is_required: bool = True
    unique_id: str | None = None
    file_type = "csv"

    spec: dict[str, Any] = {
        "trip_id": pa.string(),
        "arrival_time": pa.string(),
        "departure_time": pa.string(),
        "stop_id": pa.string(),
        "stop_sequence": pa.uint16(),
        "stop_headsign": pa.string(),
        "pickup_type": pa.uint8(),
        "drop_off_type": pa.uint8(),
        "continuous_pickup": pa.uint8(),
        "continuous_drop_off": pa.uint8(),
        "shape_dist_traveled": pa.float32(),
        "timepoint": pa.bool_(),
    }
    boolean_cols: dict[str, tuple[Any, Any]] | None = None
    defaults: dict[str, Any] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name

    def set_sequence_id(
        self, id: str = "trip_id", name: str = "seq_id"
    ) -> pd.DataFrame:
        """returns an index for each continuous sequence"""
        df = self.data

        sequenceMask = df.stop_sequence < df.stop_sequence.shift(1)
        tripMask = df[id] != df[id].shift(1)

        # test if stop_sequence is increasing
        increasing_test = sequenceMask & tripMask
        if not increasing_test.any():
            transitlog.info(
                f"{self.base_name} : Reorder not properly sorted stop sequences"
            )
            df = df.sort_values([id, "stop_sequence"], ascending=True)
            sequenceMask = df.stop_sequence < df.stop_sequence.shift(1)
            tripMask = df[id] != df[id].shift(1)

        index = sequenceMask | tripMask
        df[name] = index.fillna(False).astype("uint32[pyarrow]").cumsum()
        self.data = df
        return None

    def filter_trips(self, trip_ids: pd.Series) -> None:
        """Filter stop_times to keep only specified trip_ids"""
        df = self.data
        unique_trip_ids = trip_ids.unique()
        df = df.loc[df["trip_id"].isin(unique_trip_ids)]

        if len(df) == 0:
            raise ValueError(
                f"{self.base_name} /No stop_times found for specified trip_ids"
            )

        self.data = df

    def fill_missing_times(self) -> None:
        """Fill missing departure or arrival times - NOT IMPLEMENTED"""
        raise NotImplementedError("fill_missing_times method is not implemented")

    def drop_single_sequence(self, sequence_id: str = "seq_id") -> None:
        """Drop sequences with only one stop_sequence"""

        if sequence_id not in self.data.columns:
            raise ValueError(
                f"{self.base_name} : {sequence_id} column not found in stop_times"
            )

        df = self.data
        # verify that sequences have at least 2 stop_times
        sequence = df.groupby(sequence_id).size()

        sequence = sequence.loc[sequence == 1]
        errors = len(sequence)
        if errors > 0:
            transitlog.info(
                f"{self.base_name} : {errors} sequences with 1 stop removed"
            )
            df = df.loc[~df[sequence_id].isin(sequence.index)]

        self.data = df.loc[~df[sequence_id].isin(sequence.index)]

    def drop_invalid_shapedist(self) -> None:
        """Replace shape_dist_traveled by na if non increasing values"""

        if "shape_dist_traveled" not in self.data:
            return None

        df = self.data

        same_seq = df["stop_sequence"] < df["stop_sequence"].shift(-1)

        errors = (
            (df.shape_dist_traveled.notna())
            & (same_seq)
            & (df.shape_dist_traveled >= df.shape_dist_traveled.shift(-1))
        )

        err = len(df.loc[errors])
        if err == 0:
            return df
        transitlog.info(
            f"{self.base_name} : {err} stop_times shape_dist are not increasing"
        )
        df.loc[errors, "shape_dist_traveled"] = pd.NA

        self.data = df
        return None

    def drop_duplicate_stops(self) -> None:
        """Drop duplicated consecutive stops in a sequence"""
        df = self.data

        ref_length = len(df)
        same_stop = df["stop_id"] == df["stop_id"].shift(-1)
        same_seq = df["stop_sequence"] < df["stop_sequence"].shift(-1)
        df = df.loc[~((same_stop) & (same_seq))].copy()
        if len(df) != ref_length:
            transitlog.info(
                f"{self.base_name} : {ref_length - len(df)} identical consecutive stop_id dropped"
            )

        self.data = df


class GTFSCalendarParser(GTFSFileParser):
    """Class to parse GTFS Calendar.txt file"""

    filename: str = "calendar.txt"
    is_required: bool = False
    unique_id: str | None = "service_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "service_id": pa.string(),
        "monday": pa.bool_(),
        "tuesday": pa.bool_(),
        "wednesday": pa.bool_(),
        "thursday": pa.bool_(),
        "friday": pa.bool_(),
        "saturday": pa.bool_(),
        "sunday": pa.bool_(),
        "start_date": pa.timestamp("s"),  # pa.date32(),
        "end_date": pa.timestamp("s"),  # pa.date32(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    WEEKDAYS = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name

    def expand(self) -> pd.Series:
        """expand calendar to individual dates"""
        calendar = self.data.copy()

        # dt pandas functions are slow with pyarrow data, use pa.compute
        days = pa.compute.days_between(
            pa.array(calendar["start_date"]), pa.array(calendar["end_date"])
        )
        days = pa.compute.add(days, 1)
        calendar["days"] = pd.Series(days, index=calendar.index)

        # duplicate each row by the number of days
        calendar["_shift"] = calendar["days"].apply(lambda x: range(x))
        calendar = calendar.explode("_shift")

        # shift date
        shifted = pa.array(calendar["start_date"]).cast(pa.int64())
        shifted = pa.compute.add(
            shifted,
            pa.array(
                pa.compute.multiply(24 * 3600, pa.array(calendar["_shift"])),
                type=pa.int64(),
            ),
        )
        shifted = shifted.cast(pa.timestamp(unit="s"))

        calendar["date"] = pd.Series(
            shifted, index=calendar.index, dtype=pd.ArrowDtype(pa.timestamp(unit="s"))
        )

        calendar = calendar.drop(columns=["_shift", "start_date", "end_date", "days"])

        calendar["_dayofweek"] = calendar["date"].dt.dayofweek
        day_map = {num: day for num, day in zip(range(7), self.WEEKDAYS)}
        calendar["_dayofweek"] = calendar["_dayofweek"].map(day_map)

        mask = pd.Series(False, index=calendar.index)
        for day in self.WEEKDAYS:
            mask = mask | ((calendar["_dayofweek"] == day) & (calendar[day]))
        calendar = calendar.loc[mask, ["service_id", "date"]]

        transitlog.info(
            f"{self.base_name} : Calendar from {calendar['date'].dt.date.min()} to {calendar['date'].dt.date.max()}"
        )

        return calendar

    def has_inverted_start_end_dates(self) -> bool:
        df = self.data

        if df is None:
            return False

        mask = df["start_date"] > df["end_date"]
        return mask.any()

    def fix_start_end_dates(self) -> pd.DataFrame | None:
        """Validate that start_date is before end_date, reverse start and end dates if not"""

        if self.data is None:
            return None

        df = self.data.copy()

        # invert start and end date if inverted
        mask = df["start_date"] > df["end_date"]
        if not mask.any():
            return None

        temp = df.loc[mask, "start_date"].copy()
        df.loc[mask, "start_date"] = df["end_date"]
        df.loc[mask, "end_date"] = temp
        self.data = df

        return None


class GTFSCalendarDatesParser(GTFSFileParser):
    """Class to parse GTFS CalendarDates.txt file"""

    filename: str = "calendar_dates.txt"
    is_required: bool = False
    unique_id: str | None = "service_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "service_id": pa.string(),
        "date": pa.timestamp("s"),  # pa.date32(),
        "exception_type": pa.uint8(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name


class GTFSShapesParser(GTFSFileParser):
    """Class to parse GTFS Shapes.txt file"""

    filename: str = "shapes.txt"
    is_required: bool = False
    unique_id: str | None = "shape_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "shape_id": pa.string(),
        "shape_pt_lat": pa.float64(),
        "shape_pt_lon": pa.float64(),
        "shape_pt_sequence": pa.uint32(),
        "shape_dist_traveled": pa.float32(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name

    def filter_shape_ids(self, shape_ids: pd.Series) -> None:
        """Filter shapes to keep only specified shape_ids"""
        df = self.data
        unique_shape_ids = shape_ids.unique()

        df = df.loc[df["shape_id"].isin(unique_shape_ids)]

        if len(df) == 0:
            raise ValueError("No shapes found for specified shape_ids")

        self.data = df

    def has_single_point(self) -> pd.Series | None:
        """return shape_ids with only one point, return None if no shape data"""
        if self.data is None:
            return None

        df = self.data.groupby("shape_id").size()
        df = df.loc[df == 1]
        return pd.Series(df.index)

    def drop_single_point(self) -> None:
        """drop shapes with only one point"""
        if self.data is None:
            return None  # noqa: RET501
        singles = self.has_single_point()

        if len(singles) > 0:
            transitlog.info(
                f"{self.base_name} : {len(singles)} shape_ids contained only one point"
            )

        df = self.data.loc[~self.data.shape_id.isin(singles)]
        self.data = df

    def fill_shape_dist(self) -> None:
        """Add missing shape_distances"""

        # fill all values of shape_dist to match crs
        shp = self.data.sort_values(["shape_id", "shape_pt_sequence"])
        shp = shp.reset_index(drop=True)

        mask = shp.shape_dist_traveled.isna()
        next = shp[["shape_pt_lat", "shape_pt_lon"]].shift(1)
        shp.loc[mask, "shape_dist_traveled"] = geographic_distance(
            shp.shape_pt_lat, shp.shape_pt_lon, next.shape_pt_lat, next.shape_pt_lon
        )

        # first point in shape_id has distance = 0
        newshape = shp.shape_id != shp.shape_id.shift(1)
        shp.loc[(newshape) & (mask), "shape_dist_traveled"] = 0.0
        shp.loc[mask, "shape_dist_traveled"] = shp.groupby("shape_id")[
            "shape_dist_traveled"
        ].cumsum()

        self.data = shp


class GTFSFeedInfoParser(GTFSFileParser):
    """Class to parse GTFS FeedInfo.txt file"""

    filename: str = "feed_info"
    is_required: bool = False
    unique_id: str | None = None
    file_type = "csv"

    spec: dict[str, Any] = {
        "feed_publisher_name": pa.string(),
        "feed_publisher_url": pa.string(),
        "feed_lang": pa.string(),
        "feed_start_date": pa.timestamp("s"),
        "feed_end_date": pa.timestamp("s"),
        "feed_version": pa.string(),
        "default_lang": pa.string(),
        "feed_contact_email": pa.string(),
        "feed_contact_url": pa.string(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name


class GTFSTransfersParser(GTFSFileParser):
    """Class to parse GTFS Transfers.txt file"""

    filename: str = "transfers"
    is_required: bool = False
    unique_id: str | None = None
    file_type = "csv"

    spec: dict[str, Any] = {
        "from_stop_id": pa.string(),
        "to_stop_id": pa.string(),
        "from_route_id": pa.string(),
        "to_route_id": pa.string(),
        "from_trip_id": pa.string(),
        "to_trip_id": pa.string(),
        "transfer_type": pa.uint8(),
        "min_transfer_time": pa.uint16(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name


class GTFSLevelsParser(GTFSFileParser):
    """Class to parse GTFS Levels.txt file"""

    filename: str = "levels"
    is_required: bool = False
    unique_id: str | None = "level_id"
    file_type = "csv"

    spec: dict[str, Any] = {
        "level_id": pa.string(),
        "level_index": pa.int16(),
        "level_name": pa.string(),
    }
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
        )
        self.base_name = base_name
