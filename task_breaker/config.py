from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TASK_BREAKER_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Path.home() / ".task-breaker"
    model: str = "gpt-4.1"
    workiq_command: str = "npx"
    workiq_args: List[str] = ["-y", "@microsoft/workiq", "mcp"]
    auto_breakdown_enabled: bool = True
    auto_breakdown_threshold_days: int = 3
    check_interval_hours: int = 1
    host: str = "127.0.0.1"
    port: int = 8000
    max_level: int = 3
    max_tasks_per_level: str = "5-L"
    debug: bool = False

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir}/tasks.db"

    @property
    def workiq_eula_path(self) -> Path:
        return self.data_dir / "workiq_eula.json"


settings = Settings()
