from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.dataset as ds

from ..utils.ids import filter_ids

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

    def filter_gids(self, gids) -> "Stops":
        """Return a new Stops object only with gids"""
        results = filter_ids(self.data, gids)
        return Stops(results, self.year, self.week)
