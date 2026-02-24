from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://codityai:codityai@db:5432/codityai"

    # LLM (LiteLLM proxy via OpenAI-compatible client)
    OPENAI_API_BASE: str = "http://localhost:4000/v1"
    OPENAI_API_KEY: str = "sk-placeholder"
    OPENAI_MODEL: str = "gpt-4o-mini"

    # GitHub integration
    GITHUB_TOKEN: str = ""
    GITHUB_REPO: str = ""  # format: "owner/repo"

    # Prometheus integration
    PROMETHEUS_ENDPOINT: str = ""  # e.g. "http://prometheus:9090"
    PROMETHEUS_POLL_INTERVAL_SECONDS: int = 60

    # AI Chat
    AI_MAX_TOOL_ROUNDS: int = 5
    AI_MAX_HISTORY_MSGS: int = 30

    # App
    APP_ENV: str = "development"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
