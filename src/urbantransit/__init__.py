__all__ = [
    "GTFStoParquet",
    "Transit",
    "TransitGraph",
    "clean_gtfs_folder",
    "parse_gtfs_folder",
]


def __getattr__(name: str):
    if name == "TransitGraph":
        from .graph import TransitGraph

        return TransitGraph
    if name == "GTFStoParquet":
        from .parsers import GTFStoParquet

        return GTFStoParquet
    if name == "clean_gtfs_folder":
        from .gtfs_utils import clean_gtfs_folder

        return clean_gtfs_folder
    if name == "parse_gtfs_folder":
        from .gtfs_utils import parse_gtfs_folder

        return parse_gtfs_folder
    if name == "Transit":
        from .transit import Transit

        return Transit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
