"""Stable typed response envelopes for OpenAPI/frontend client generation."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Resource(ResponseModel):
    id: str
    kind: Literal["world", "dataset", "model"]
    name: str
    status: str
    metadata: dict[str, Any]
    created_at: str


class ResourceList(ResponseModel):
    items: list[Resource]
    total: int
    limit: int
    offset: int


class Job(ResponseModel):
    id: str
    kind: str
    state: Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled"]
    progress: float = Field(ge=0, le=1)
    stage: str
    message: str
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    heartbeat_at: str | None
    worker_id: str | None


class JobList(ResponseModel):
    items: list[Job]
    total: int
    limit: int
    offset: int


class ForecastRow(ResponseModel):
    timestamp: str
    zone_id: str
    horizon: int
    predicted_demand: float = Field(ge=0)
    lower: float = Field(ge=0)
    upper: float = Field(ge=0)


class Forecast(ResponseModel):
    schema_version: str
    model_id: str
    dataset_id: str
    world_id: str
    source: Literal["synthetic", "imported"]
    target_semantics: Literal["requests", "completed_trips"]
    forecast_origin: str
    bucket_minutes: int
    timezone: str
    horizon: int
    rows: list[ForecastRow]
    totals: list[dict[str, Any]]
    interval: dict[str, Any]
    exogenous_assumption: Any
    scenario: dict[str, Any]


class GeoJSON(ResponseModel):
    type: Literal["FeatureCollection"]
    features: list[dict[str, Any]]


class WorldMap(ResponseModel):
    world_id: str
    crs: str
    zones: GeoJSON
    roads: GeoJSON
    is_virtual: bool


class LearningHistory(ResponseModel):
    candidates: list[dict[str, Any]]
    epochs: list[dict[str, Any]]


class ErrorDetail(ResponseModel):
    code: str
    message: str
    details: Any = None


class ErrorEnvelope(ResponseModel):
    error: ErrorDetail
    request_id: str | None
