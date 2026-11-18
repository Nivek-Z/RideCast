"""Request limits and comparable experimental settings."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Seed = Annotated[int, Field(strict=True, ge=0, le=2**31-1)]
Worlds = Literal[8, 16, 32, 64]
Alpha = Literal[0.1, 0.3, 1.0, 3.0]
RESEARCH_MODES = ("reproduction", "arena")


class ArenaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worlds_per_seed: Worlds = 16
    teacher_alpha: Alpha = 1.0
    seeds: list[Seed] = Field(default_factory=lambda: [7, 42, 99], min_length=1, max_length=5)

    @model_validator(mode="after")
    def normalize_seeds(self):
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("随机种子须互不重复")
        self.seeds.sort()
        return self


class ArenaBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configurations: list[ArenaConfig] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def comparable(self):
        first = self.configurations[0].seeds
        if any(config.seeds != first for config in self.configurations):
            raise ValueError("同批实验须使用同一组随机种子")
        if len({(c.worlds_per_seed, c.teacher_alpha) for c in self.configurations}) != len(self.configurations):
            raise ValueError("批次中有重复配置")
        return self
