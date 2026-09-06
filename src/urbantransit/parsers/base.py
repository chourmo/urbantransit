from typing import Any

import pandas as pd
import pyarrow as pa


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
        file_type='csv',
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

        self.filename = filename
        self.spec = spec
        self.defaults = defaults
        self.boolean_cols = boolean_cols
        self.is_required = is_required
        self.unique_id = unique_id
        self.file_type = file_type
        self.base_name = base_name

        if df is None:
            if is_required:
                raise ValueError(f'{filename} is required but missing')
            else:
                self.empty = True
                df = self.empty_frame()
        else:
            self.empty = False

        # check unicity of unique_ids
        if unique_id is not None:
            if not unique_id not in df.columns:
                raise ValueError(f'{unique_id} is missing in {filename}')
            if not df[unique_id].is_unique:
                raise ValueError(f'{unique_id} is not unique in {filename}')

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

    def to_file(self, path_or_buf, sep=','):
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

        if self.file_type == 'csv':
            df.to_csv(path_or_buf, sep=sep, index=False)


class _ConfiguredGTFSFileParser(GTFSFileParser):
    filename: str = ''
    is_required: bool = True
    unique_id: str | None = None
    file_type = 'csv'
    spec: dict[str, Any] = {}
    defaults: dict[str, Any] | None = None
    boolean_cols: dict[str, tuple[Any, Any, Any]] | None = None

    def __init__(self, df: pd.DataFrame | None, base_name: str | None = None):
        super().__init__(
            df,
            filename=self.filename,
            spec=self.spec,
            defaults=self.defaults,
            boolean_cols=self.boolean_cols,
            is_required=self.is_required,
            file_type=self.file_type,
            base_name=base_name,
        )
        self.base_name = base_name
