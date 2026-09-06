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

    def __init__(self, path: Path, fix_inner_folder=False):
        """init from a path to a zip file or a folder"""

        self.path = path
        self.base_name = path.stem

        if not self.is_dir() and not self.is_zip():
            raise ValueError(f'{path} is neither a zip file or a directory')

        if fix_inner_folder and self.has_inner_folder():
            self.fix_dir_in_zip()

        # cache filenames
        self.files = self._filenames()

        if not self.has_required_files():
            raise ValueError(f'{path} is missing required files')

    # validate path is a GTFS dir or zip file
    def is_dir(self):
        return self.path.is_dir()

    def is_zip(self):
        return self.path.is_file() and zipfile.is_zipfile(self.path)

    def _filenames(self):
        if self.is_dir():
            return {f.name for f in self.path.iterdir() if f.is_file()}
        if self.is_zip():
            with zipfile.ZipFile(self.path, 'r') as archive:
                files = {x for x in archive.namelist()}
            return files

    def has_required_files(self):
        """Verify if all required files exist"""

        # test if all required files are in file
        if not self.files.issuperset(set(self.required_files)):
            return False

        # test if either calendar.txt or calendar_dates.txt exist
        return 'calendar.txt' in self.files or 'calendar_dates.txt' in self.files

    def calendar_statistics(self):
        """return a dataframe of week, year and number of trips"""
        calendars = self.merged_calendars()
        trips = self.get_trips().data

        calendars = calendars.groupby(['week', 'year', 'service_id']).size()
        calendars = calendars.to_frame('size').reset_index()

        df = pd.merge(trips, calendars, on='service_id', how='left')
        df = df.groupby(['year', 'week'], sort='ascending')['size'].sum()

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
            cal = pd.merge(cal, cal_dates, on=['service_id', 'date'], how='outer')

            # drop if exception_type is 2
            cal = cal.loc[(cal.exception_type == 1) | (cal.exception_type.isna())]
            del cal['exception_type']

        elif cal is None:
            cal = cal_dates.loc[cal_dates.exception_type == 1].copy()
            del cal['exception_type']

        cal = cal.reset_index(drop=True)
        isocal = cal['date'].dt.isocalendar()
        cal['week'] = isocal.week
        cal['year'] = isocal.year

        return cal

    def _get_parser(self, file_class):
        name = file_class.filename
        spec = file_class.spec

        if name not in self.files:
            return file_class(None)

        if file_class.file_type == 'csv':
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
            timestamp_parsers=['%Y%m%d'],
        )
        parse = csv.ParseOptions(delimiter=',')

        if self.is_zip():
            with zipfile.ZipFile(self.path, 'r') as archive:
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
        temp_path = self.path.with_stem(self.path.stem + '_temp')

        with (
            zipfile.ZipFile(self.path, 'r') as archive,
            zipfile.ZipFile(
                temp_path, 'w', compression=zipfile.ZIP_BZIP2, compresslevel=8
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
        if 'stop_code' not in stops.columns or stops['stop_code'].isnull().all():
            return False

        df = self.get_stoptimes().data
        df = df['stop_id'].drop_duplicates()

        match_ids = len(df.loc[df.isin(stops['stop_id'])])
        match_codes = len(df.loc[df.isin(stops['stop_code'])])

        return match_ids < match_codes

    def has_inner_folder(self):
        """test if there is an inner directory in zip file"""

        if not self.is_zip():
            return False

        with zipfile.ZipFile(self.path, 'r') as archive:
            files = [zipfile.Path(archive, x).is_dir() for x in archive.namelist()]
        return any(files)

    def fix_dir_in_zip(self):
        if not self.is_zip() or not self.has_inner_folder():
            return None  # noqa: RET501

        # extract files from zip in temporary folder
        temp_path = self.path.with_stem(self.path.stem + '_temp')

        with (
            zipfile.ZipFile(self.path, 'r') as archive,
            zipfile.ZipFile(
                temp_path, 'w', compression=zipfile.ZIP_BZIP2, compresslevel=8
            ) as new_archive,
        ):
            for file in [
                x for x in archive.namelist() if not zipfile.Path(archive, x).is_dir()
            ]:
                new_archive.writestr(
                    zipfile.Path(archive, file).name, archive.open(file).read()
                )

        self.path = temp_path.replace(self.path)
