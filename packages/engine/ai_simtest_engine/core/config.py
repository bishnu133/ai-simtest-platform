"""
Application configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application settings."""

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Database
    database_url: str = "sqlite+aiosqlite:///./simtest.db"

    # Redis (optional)
    redis_url: str | None = None

    # LLM API Keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Default models
    persona_generator_model: str = "gpt-4-turbo"
    user_simulator_model: str = "gpt-4-turbo"
    quality_judge_model: str = "gpt-4-turbo"

    # Rate limits
    max_parallel_conversations: int = 10
    max_parallel_judges: int = 5
    llm_requests_per_minute: int = 500

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
