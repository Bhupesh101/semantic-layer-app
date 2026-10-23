"""Semantic query spec (P6-01). These models also generate the tool JSON schemas (P6-13)."""

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

FilterOp = Literal[
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "between",
    "is_null",
    "is_not_null",
]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]


class Filter(BaseModel):
    field: str = Field(description="Column, metric, synonym or display name to filter on.")
    op: FilterOp = "="
    value: Optional[Any] = Field(
        None,
        description="Scalar for comparisons, a list for in/not_in, [low, high] for between, "
        "omitted for is_null/is_not_null. Use exact stored values (see list_dimension_values).",
    )


class TimeRange(BaseModel):
    start: Optional[str] = Field(None, description="Inclusive ISO date/datetime, e.g. 2011-01-01")
    end: Optional[str] = Field(None, description="Exclusive ISO date/datetime, e.g. 2012-01-01")


class OrderBy(BaseModel):
    field: str = Field(description="A requested metric or dimension name.")
    direction: Literal["asc", "desc"] = "desc"


class SemanticQuery(BaseModel):
    metrics: List[str] = Field(
        default_factory=list,
        max_length=10,
        description="Metric names, measure column names (default aggregation), "
        "'agg(column)' with agg in sum/avg/min/max/count/count_distinct, or 'row_count'.",
    )
    dimensions: List[str] = Field(default_factory=list, max_length=10, description="Columns to group by.")
    filters: List[Filter] = Field(default_factory=list, max_length=20)
    time_dimension: Optional[str] = Field(None, description="Date/datetime column for time_grain and time_range.")
    time_grain: Optional[TimeGrain] = None
    time_range: Optional[TimeRange] = None
    order_by: List[OrderBy] = Field(default_factory=list, max_length=5)
    limit: Optional[int] = Field(None, ge=1, le=10_000)
