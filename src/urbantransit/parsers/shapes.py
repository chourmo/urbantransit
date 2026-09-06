import pandas as pd
from geometryhelpers import geographic_distance

from urbantransit.utils.logging import transitlog

from .base import _ConfiguredGTFSFileParser
from .shapes_schema import SHAPES_BOOLEAN_COLS, SHAPES_DEFAULTS, SHAPES_SPEC


class GTFSShapesParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Shapes.txt file"""

    filename: str = 'shapes.txt'
    is_required: bool = False
    unique_id: str | None = 'shape_id'
    file_type = 'csv'
    spec = SHAPES_SPEC
    defaults = SHAPES_DEFAULTS
    boolean_cols = SHAPES_BOOLEAN_COLS

    def filter_shape_ids(self, shape_ids: pd.Series) -> None:
        """Filter shapes to keep only specified shape_ids"""
        df = self.data
        unique_shape_ids = shape_ids.unique()

        df = df.loc[df['shape_id'].isin(unique_shape_ids)]

        if len(df) == 0:
            raise ValueError('No shapes found for specified shape_ids')

        self.data = df

    def has_single_point(self) -> pd.Series | None:
        """return shape_ids with only one point, return None if no shape data"""
        if self.data is None:
            return None

        df = self.data.groupby('shape_id').size()
        df = df.loc[df == 1]
        return pd.Series(df.index)

    def drop_single_point(self) -> None:
        """drop shapes with only one point"""
        if self.data is None:
            return None  # noqa: RET501
        singles = self.has_single_point()

        if len(singles) > 0:
            transitlog.info(
                f'{self.base_name} : {len(singles)} shape_ids contained only one point'
            )

        df = self.data.loc[~self.data.shape_id.isin(singles)]
        self.data = df

    def fill_shape_dist(self) -> None:
        """Add missing shape_distances"""

        # fill all values of shape_dist to match crs
        shp = self.data.sort_values(['shape_id', 'shape_pt_sequence'])
        shp = shp.reset_index(drop=True)

        mask = shp.shape_dist_traveled.isna()
        next = shp[['shape_pt_lat', 'shape_pt_lon']].shift(1)
        shp.loc[mask, 'shape_dist_traveled'] = geographic_distance(
            shp.shape_pt_lat, shp.shape_pt_lon, next.shape_pt_lat, next.shape_pt_lon
        )

        # first point in shape_id has distance = 0
        newshape = shp.shape_id != shp.shape_id.shift(1)
        shp.loc[(newshape) & (mask), 'shape_dist_traveled'] = 0.0
        shp.loc[mask, 'shape_dist_traveled'] = shp.groupby('shape_id')[
            'shape_dist_traveled'
        ].cumsum()

        self.data = shp
