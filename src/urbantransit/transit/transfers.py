from pathlib import Path

import listandstruct as ls
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from geometryhelpers import first_point, last_point, pairs

from ..constants import LAST_STOP
from ..utils.logging import transitlog

DISTANCE_TYPE = float | pd.Series | dict[str, float]

class Transfers:
    """Class to store transfer dataframe and metadata used to create it"""

    FILE = "transfers.parquet"

    def __init__(
        self,
        data: pd.DataFrame,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
        crs: str,
    ):
        self.data = data
        self.min_transfer = min_transfer
        self.max_transfer = max_transfer
        self.dists = dists
        self.crs = crs

    @classmethod
    def from_lines(
        cls,
        lines: pd.DataFrame,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
        crs: str,
    ) -> "Transfers":
        """init transfers from Lines and transfer parameters"""

        line_crs = lines.geometry.crs

        cols = [
            "line_gid",
            "route_type",
            "geometry",
            "stop_sequence",
            "departure",
            "travel",
            "direction_id",
            "route_gid",
        ]

        df = lines[cols].copy()
        df["_arrival"] = df["departure"].listarray.add(df["travel"])
        df["_arrival"] = df["_arrival"].listarray.add(min_transfer)
        df["_min_arr"] = df["_arrival"].listarray.aggregate("min")
        df["_max_arr"] = df["_arrival"].listarray.aggregate("max")
        df["_min_dep"] = df["departure"].listarray.aggregate("min")
        df["_max_dep"] = df["departure"].listarray.aggregate("max")

        # add distance column
        df["max_dist"] = cls.map_distance(df, dists)

        # transfer sources cannot be first stop of a line
        # if lines have just one arc, then stop_sequence cannot be 0
        single_arc = lines[["line_gid"]].groupby("line_gid").size()
        single_arc = single_arc.loc[single_arc == 1]
        mask = (df.stop_sequence > 0) & (~df.line_gid.isin(single_arc.index))
        sources = df.loc[mask].drop(
            columns=["_min_dep", "_max_dep", "departure", "travel"]
        )
        sources["geometry"] = last_point(sources.geometry)
        sources = sources.set_geometry("geometry", crs=line_crs).to_crs(crs)

        # transfer cannot target the last arc of a line
        mask = df.stop_sequence < LAST_STOP
        targets = df.loc[mask].drop(
            columns=["_arrival", "_min_arr", "_max_arr", "travel"]
        )
        targets["geometry"] = first_point(targets.geometry)
        targets = targets.set_geometry("geometry", crs=line_crs).to_crs(crs)
        targets = targets.rename(columns={"max_dist": "to_max_dist"})

        df = pairs(
            sources,
            "max_dist",
            targets,
            "to_max_dist",
            filter_distance="highest",
            drop_same="line_gid",
        )
        df = df.drop(columns=["max_dist", "to_to_max_dist"])
        _l = len(df)
        transitlog.info(f"{_l} base transfers")

        # drop not intersecting time ranges

        df = cls._drop_time_range(df)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as incompatible time ranges")

        # filter transfers
        df = cls._drop_duplicated_lines(df)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as duplicated to same lines")

        df = cls._drop_by_time(df, max_transfer)
        transitlog.info(f"{(_l - len(df)) / _l:.1%} as out of time range")

        # compact transfers by line_gid / stop_sequence source and target
        cols = ["line_gid", "stop_sequence", "to_line_gid", "to_stop_sequence"]
        grpcols = ["line_gid", "stop_sequence"]
        to_line = ls.list_array(df["to_line_gid"], df[grpcols], ignore_index=True)
        to_seq = ls.list_array(
            df["to_stop_sequence"], offsets=to_line, ignore_index=True
        )
        df = df[cols].drop_duplicates(subset=grpcols, ignore_index=True)
        df["to_line_gid"] = to_line
        df["to_stop_sequence"] = to_seq

        return cls(df.reset_index(drop=True), min_transfer, max_transfer, dists, crs)

    @staticmethod
    def _drop_duplicated_lines(pairs: pd.DataFrame) -> pd.DataFrame:
        """Drop duplicates from_line_gid / to_line_gid, keep closest by distance"""

        cols = ["line_gid", "stop_sequence", "to_line_gid"]
        df = pairs.sort_values(cols + ["distance"], ascending=True)
        df = df.drop_duplicates(subset=cols, keep="first")

        return df

    @staticmethod
    def _drop_by_time(pairs: pd.DataFrame, max_transfer: int) -> pd.DataFrame:
        """Drop transfers pairs if source and target timetables do no match, within min_transfer and max_transfer times"""

        df = pairs.copy()

        # remove pairs where the smallest transfer waits is too high
        df["_tr_time"] = df["_arrival"].listarray.match(df["to_departure"])

        diff = df["_tr_time"].listarray.subtract(df["_arrival"])
        diff = diff.listarray.aggregate("min")

        df = df.loc[diff <= max_transfer].copy()
        del df["_tr_time"]

        return df

    @staticmethod
    def _drop_time_range(pairs: pd.DataFrame) -> pd.DataFrame:
        mask = (pairs._max_arr < pairs.to__min_dep) | (
            pairs._min_arr > pairs.to__max_dep
        )
        df = pairs.loc[~(mask)].copy()
        df = df.drop(
            columns=[
                "_min_arr",
                "_max_arr",
                "to__min_dep",
                "to__max_dep",
            ]
        )

        return df

    @staticmethod
    def map_distance(df: pd.DataFrame, distance: DISTANCE_TYPE) -> pd.Series:
        """Return a distance column from distance argument, use maximum distance for missing route_types

        Arguments :
            distance:
                dictionary of distances by route_type or integer distance, in projected crs units

            Returns:
                a Series of distances"""

        if isinstance(distance, dict):
            if "route_type" not in df.columns:
                raise TypeError(
                    "If distance is a dict, route_type column must be present in lines"
                )
            dist = df["route_type"].map(distance)

            # replace missing route_types by maximum distance value
            dist.loc[dist.isna()] = max(distance.values())
            return dist
        elif isinstance(distance, (int, float)):
            return pd.Series(float(distance), index=df.index)
        else:
            raise TypeError("distance must be either a dict or a numeric value")

    @classmethod
    def from_parquet(
        cls,
        path: Path,
        crs: str,
        min_transfer: int,
        max_transfer: int,
        dists: DISTANCE_TYPE,
    ) -> "Transfers":
        """open parquet file"""
        filepath = path / cls.FILE
        try:
            table = pq.read_table(filepath)
        except (FileNotFoundError, OSError):
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to read parquet file '{filepath}': {e}") from e

        df = table.to_pandas(types_mapper=pd.ArrowDtype)
        return cls(df, min_transfer, max_transfer, dists, crs)

    def to_parquet(self, path: Path, name: str | None = None):
        """save data to parquet at path with GTFS metadata"""
        table = pa.Table.from_pandas(self.data, index=False)
        metadata = table.schema.metadata
        metadata.update(self._gtfs_metadata())
        table = table.replace_schema_metadata(metadata)
        write_path = path / self.filename(name)
        pq.write_table(table, write_path)

    # -----------------------------------------------------------------------------------

    def expand_transfers(self, lines: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame of all transfer pairs with timetable information"""

        if self.data is None:
            raise ValueError("No transfers available, run add_transfers first")

        transf = self.data.explode(["to_line_gid", "to_stop_sequence"])

        # add timetable to upstream and downstream transfers
        transf = pd.merge(
            transf,
            lines[["line_gid", "stop_sequence", "departure", "travel"]],
            left_on=["line_gid", "stop_sequence"],
            right_on=["line_gid", "stop_sequence"],
            how="left",
        )

        transf["arrival"] = transf["departure"].listarray.add(transf["travel"])
        transf = transf.drop(columns=["travel", "departure"])

        transf = pd.merge(
            transf,
            lines[["line_gid", "stop_sequence", "departure"]],
            left_on=["to_line_gid", "to_stop_sequence"],
            right_on=["line_gid", "stop_sequence"],
            how="left",
            suffixes=("", "_to"),
        )

        transf = transf.rename(columns={"departure": "to_departure"})
        transf = transf.drop(columns=["line_gid_to", "stop_sequence_to"])
        transf = transf.reset_index(drop=True)

        # drop transfers with non matching timetables
        transf["_threshold"] = transf["arrival"].listarray.add(self.min_transfer)
        mask = transf["_threshold"].listarray.intersects(transf["to_departure"])
        transf = transf.loc[mask].copy()

        # corresponding departure timepoints array
        transf["to_timepoint"] = transf["_threshold"].listarray.match(
            transf["to_departure"]
        )
        del transf["_threshold"]

        # filter NA to_timepoint and array
        na_mask = transf["to_timepoint"].listarray.isna()
        transf["to_timepoint"] = transf["to_timepoint"].listarray.filter(na_mask)
        transf["arrival"] = transf["arrival"].listarray.filter(na_mask)
        transf = transf.dropna(subset="to_timepoint")

        return transf.reset_index(drop=True)
