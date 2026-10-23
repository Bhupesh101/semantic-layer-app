"""Semantic model JSON schema (spec 3.1), as Pydantic models.

Deviation from the spec sketch: allowed values live on each column (`allowed_values`)
instead of a top-level `dimensions_allowed_values` map, so a column is self-contained.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DataType = Literal["string", "integer", "float", "boolean", "date", "datetime"]
Role = Literal["dimension", "measure", "time_dimension", "identifier", "ignore"]
Aggregation = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
Confidence = Literal["high", "medium", "low"]
Provenance = Literal["ai", "user_override", "ai_regenerated", "user"]
ReviewStatus = Literal["pending", "accepted"]

IDENT = r"^[a-z_][a-z0-9_]*$"

# Fields a reviewer can edit; provenance is tracked per field (P4-08)
COLUMN_EDITABLE = [
    "display_name",
    "description",
    "data_type",
    "role",
    "default_aggregation",
    "unit",
    "format",
    "synonyms",
    "allowed_values",
    "pii",
]
METRIC_EDITABLE = ["display_name", "description", "expression", "filters", "unit", "synonyms"]
ENTITY_EDITABLE = ["display_name", "description", "grain"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Entity(_Base):
    name: str = Field(pattern=IDENT, max_length=64)
    display_name: str = ""
    description: str = ""
    grain: str = ""
    confidence: Optional[Confidence] = None
    rationale: Optional[str] = None
    provenance: Dict[str, Provenance] = Field(default_factory=dict)


class Column(_Base):
    name: str
    display_name: str = ""
    description: str = ""
    data_type: DataType = "string"
    role: Role = "dimension"
    default_aggregation: Optional[Aggregation] = None
    unit: Optional[str] = None
    format: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    allowed_values: Optional[List[str]] = None
    pii: bool = False
    confidence: Optional[Confidence] = None
    rationale: Optional[str] = None
    provenance: Dict[str, Provenance] = Field(default_factory=dict)
    review_status: ReviewStatus = "pending"

    @field_validator("synonyms", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        return v or []

    @field_validator("allowed_values", mode="before")
    @classmethod
    def _stringify(cls, v):
        return None if v is None else [str(x) for x in v]


class Metric(_Base):
    name: str = Field(pattern=IDENT, max_length=64)
    display_name: str = ""
    description: str = ""
    expression: str
    filters: List[str] = Field(default_factory=list)
    unit: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    confidence: Optional[Confidence] = None
    rationale: Optional[str] = None
    provenance: Dict[str, Provenance] = Field(default_factory=dict)
    review_status: ReviewStatus = "pending"

    @field_validator("filters", "synonyms", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        return v or []


class SemanticModel(_Base):
    entity: Entity
    columns: List[Column]
    metrics: List[Metric] = Field(default_factory=list)
    relationships: List[dict] = Field(default_factory=list)  # reserved for P9
    sample_questions: List[str] = Field(default_factory=list)

    def column(self, name: str) -> Optional[Column]:
        return next((c for c in self.columns if c.name == name), None)

    def metric(self, name: str) -> Optional[Metric]:
        return next((m for m in self.metrics if m.name == name), None)


def llm_output_schema() -> dict:
    """The JSON shape we ask the LLM to produce (review-only fields removed)."""
    return {
        "entity": {
            "name": "snake_case table name",
            "display_name": "str",
            "description": "str",
            "grain": "what one row represents",
            "confidence": "high|medium|low",
            "rationale": "str, required when confidence is low",
        },
        "columns": [
            {
                "name": "exact column name from the profile",
                "display_name": "str",
                "description": "str",
                "data_type": "string|integer|float|boolean|date|datetime",
                "role": "dimension|measure|time_dimension|identifier|ignore",
                "default_aggregation": "sum|avg|min|max|count|count_distinct (measures only, else null)",
                "unit": "str or null (e.g. GBP, units, %)",
                "format": "str or null (e.g. %Y-%m-%d)",
                "synonyms": ["other names users may use"],
                "allowed_values": ["only for low-cardinality categoricals, else null"],
                "pii": "bool",
                "confidence": "high|medium|low",
                "rationale": "str, required when confidence is low",
            }
        ],
        "metrics": [
            {
                "name": "snake_case",
                "display_name": "str",
                "description": "str",
                "expression": "DuckDB aggregate SQL over column names, e.g. SUM(amount)",
                "filters": ["optional SQL boolean conditions, e.g. status = 'completed'"],
                "unit": "str or null",
                "synonyms": ["str"],
                "confidence": "high|medium|low",
                "rationale": "str or null",
            }
        ],
        "sample_questions": ["5-10 natural-language questions answerable with this model"],
    }
