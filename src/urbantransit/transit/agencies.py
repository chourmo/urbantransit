from pathlib import Path

import geopandas as gpd
import listandstruct as ls
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from geometryhelpers import group_boundingbox

from ..utils.ids import filter_ids


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
    def from_parquet_dataset(cls, path: str | Path, year: int, week: int) -> "Agencies":
        """Create Agencies from a dataset for year and week"""

        file_path = Path(path) / cls.DATASET

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
    def from_parquet(cls, path: str | Path, year: int, week: int) -> "Agencies":
        """Create Agencies from a path directory"""

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
                f"Failed to read Agencies parquet file '{file_path}': {e}"
            ) from e

    def to_parquet(self, path: str | Path):
        """Save to geoparquet file."""

        path = Path(path)

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
        routes = routes.drop(columns=["agency_name"], errors="ignore")
        agency = self.data.drop(columns=["routes", "bbox"])
        df = pd.merge(routes, agency, on="agency_gid", how="left").reset_index()
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
