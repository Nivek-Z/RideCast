"""Environment configuration; all application state lives under RIDECAST_HOME."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path = field(default_factory=lambda: Path(os.getenv("RIDECAST_HOME", "var")).resolve())
    api_key: str = field(default_factory=lambda: os.getenv("RIDECAST_API_KEY", ""))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip() for x in os.getenv("RIDECAST_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
        if x.strip()
    ))
    max_upload_bytes: int = 32 * 1024 * 1024
    stale_seconds: int = 120

    def initialize(self):
        for name in ("worlds", "datasets", "models", "uploads", "jobs", ".staging"):
            (self.home / name).mkdir(parents=True, exist_ok=True)

    @property
    def database(self):
        return self.home / "ridecast.sqlite3"
