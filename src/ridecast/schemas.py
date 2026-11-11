"""Versioned public request schemas; unknown fields are rejected."""

from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Event(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    zone_id: str
    start: str
    end: str
    intensity: float = Field(ge=0, le=10)


class Scenario(StrictModel):
    demand_multiplier: float = Field(default=1, gt=0, le=5)
    rain_multiplier: float = Field(default=1, ge=0, le=5)
    traffic_multiplier: float = Field(default=1, gt=0, le=3)
    supply_multiplier: float = Field(default=1, ge=0, le=3)
    holiday_dates: list[str] = Field(default_factory=list, max_length=366)
    working_dates: list[str] = Field(default_factory=list, max_length=366)
    events: list[Event] = Field(default_factory=list, max_length=100)


class WorldCreate(StrictModel):
    name: str = Field(default="霁川市", min_length=1, max_length=100)
    rows: int = Field(default=4, ge=1, le=8)
    cols: int = Field(default=4, ge=1, le=8)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    bucket_minutes: Literal[15, 30, 60] = 60
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        ZoneInfo(value)
        return value


class SimulationCreate(StrictModel):
    world_id: str
    start: str = "2026-01-01T00:00:00+08:00"
    days: int = Field(default=60, ge=2, le=180)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    scenario: Scenario = Field(default_factory=Scenario)


class TrainConfig(StrictModel):
    lookback: int = Field(default=24, ge=2, le=168)
    horizon: int = Field(default=3, ge=1, le=24)
    epochs: int = Field(default=12, ge=1, le=100)
    batch_size: int = Field(default=128, ge=16, le=1024)
    units: int = Field(default=32, ge=8, le=128)
    learning_rate: float = Field(default=0.001, gt=0, le=0.1)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    tune: bool = False


class TrainingCreate(StrictModel):
    dataset_id: str
    config: TrainConfig = Field(default_factory=TrainConfig)


class ImportOptions(StrictModel):
    observation_start: str
    observation_end: str
    coverage_attested: bool
    target_semantics: Literal["requests", "completed_trips"]
    timezone: str = "Asia/Shanghai"
    invalid_policy: Literal["reject", "drop"] = "reject"
    column_map: dict[str, str] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def confirmed(self):
        if not self.coverage_attested:
            raise ValueError("Explicit complete observation coverage is required for zero-filled demand buckets")
        ZoneInfo(self.timezone)
        return self


class ForecastRequest(StrictModel):
    model_id: str
    dataset_id: str
    horizon: int | None = Field(default=None, ge=1, le=24)
    scenario: dict | None = None


class DispatchRequest(StrictModel):
    model_id: str
    dataset_id: str
    supply: dict[str, Annotated[int, Field(strict=True, ge=0, le=1_000_000)]] | None = None


class ActivateRequest(StrictModel):
    model_id: str
