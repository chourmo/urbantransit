import pandas as pd

from urbantransit.utils.logging import transitlog

from .base import _ConfiguredGTFSFileParser
from .stop_times_schema import (
    STOP_TIMES_BOOLEAN_COLS,
    STOP_TIMES_DEFAULTS,
    STOP_TIMES_SPEC,
)


class GTFSStopTimesParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS StopTimes.txt file"""

    filename: str = 'stop_times.txt'
    is_required: bool = True
    unique_id: str | None = None
    file_type = 'csv'
    spec = STOP_TIMES_SPEC
    defaults = STOP_TIMES_DEFAULTS
    boolean_cols = STOP_TIMES_BOOLEAN_COLS

    def set_sequence_id(
        self, id: str = 'trip_id', name: str = 'seq_id'
    ) -> pd.DataFrame:
        """returns an index for each continuous sequence"""
        df = self.data

        sequenceMask = df.stop_sequence < df.stop_sequence.shift(1)
        tripMask = df[id] != df[id].shift(1)

        # test if stop_sequence is increasing
        increasing_test = sequenceMask & tripMask
        if not increasing_test.any():
            transitlog.info(
                f'{self.base_name} : Reorder not properly sorted stop sequences'
            )
            df = df.sort_values([id, 'stop_sequence'], ascending=True)
            sequenceMask = df.stop_sequence < df.stop_sequence.shift(1)
            tripMask = df[id] != df[id].shift(1)

        index = sequenceMask | tripMask
        df[name] = index.fillna(False).astype('uint32[pyarrow]').cumsum()
        self.data = df
        return None

    def filter_trips(self, trip_ids: pd.Series) -> None:
        """Filter stop_times to keep only specified trip_ids"""
        df = self.data
        unique_trip_ids = trip_ids.unique()
        df = df.loc[df['trip_id'].isin(unique_trip_ids)]

        if len(df) == 0:
            raise ValueError(
                f'{self.base_name} /No stop_times found for specified trip_ids'
            )

        self.data = df

    def fill_missing_times(self) -> None:
        """Fill missing departure or arrival times - NOT IMPLEMENTED"""
        raise NotImplementedError('fill_missing_times method is not implemented')

    def drop_single_sequence(self, sequence_id: str = 'seq_id') -> None:
        """Drop sequences with only one stop_sequence"""

        if sequence_id not in self.data.columns:
            raise ValueError(
                f'{self.base_name} : {sequence_id} column not found in stop_times'
            )

        df = self.data
        # verify that sequences have at least 2 stop_times
        sequence = df.groupby(sequence_id).size()

        sequence = sequence.loc[sequence == 1]
        errors = len(sequence)
        if errors > 0:
            transitlog.info(
                f'{self.base_name} : {errors} sequences with 1 stop removed'
            )
            df = df.loc[~df[sequence_id].isin(sequence.index)]

        self.data = df.loc[~df[sequence_id].isin(sequence.index)]

    def drop_invalid_shapedist(self) -> None:
        """Replace shape_dist_traveled by na if non increasing values"""

        if 'shape_dist_traveled' not in self.data:
            return None

        df = self.data

        same_seq = df['stop_sequence'] < df['stop_sequence'].shift(-1)

        errors = (
            (df.shape_dist_traveled.notna())
            & (same_seq)
            & (df.shape_dist_traveled >= df.shape_dist_traveled.shift(-1))
        )

        err = len(df.loc[errors])
        if err == 0:
            return df
        transitlog.info(
            f'{self.base_name} : {err} stop_times shape_dist are not increasing'
        )
        df.loc[errors, 'shape_dist_traveled'] = pd.NA

        self.data = df
        return None

    def drop_duplicate_stops(self) -> None:
        """Drop duplicated consecutive stops in a sequence"""
        df = self.data

        ref_length = len(df)
        same_stop = df['stop_id'] == df['stop_id'].shift(-1)
        same_seq = df['stop_sequence'] < df['stop_sequence'].shift(-1)
        df = df.loc[~((same_stop) & (same_seq))].copy()
        if len(df) != ref_length:
            transitlog.info(
                f'{self.base_name} : {ref_length - len(df)} identical consecutive stop_id dropped'
            )

        self.data = df
