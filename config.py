"""Configuration for think-tank."""

import dataclasses
import json
import os
from dataclasses import dataclass


@dataclass
class Config:
    # LLM
    model: str = ""                              # empty = auto-detect from /v1/models
    base_url: str = "http://localhost:1234/v1"   # LM Studio default
    generator_temperature: float = 0.95
    reviewer_temperature: float = 0.2
    llm_timeout_seconds: int = 300

    # Loop limits
    max_ideas: int = 50
    max_runtime_hours: float = 8.0
    max_retries: int = 3
    max_consecutive_failures: int = 10
    retry_sleep_seconds: int = 30

    # Paths (relative to cwd)
    task_file: str = "task.md"
    ideas_dir: str = "ideas"
    index_file: str = "ideas_index.md"
    state_file: str = ".loop_state.json"

    @classmethod
    def load(cls, path: str) -> "Config":
        """Load config from a JSON file, falling back to defaults for missing keys."""
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            known = {f.name for f in dataclasses.fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        return cls()

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(dataclasses.asdict(self), f, indent=2)
