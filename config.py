from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    Application configuration from environment variables.
    All modules must consume config from this class — no hardcoded values.
    """

    # Application
    APP_NAME: str = Field(default="sre-agent", description="Application name")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # FastAPI
    API_HOST: str = Field(default="0.0.0.0", description="API host")
    API_PORT: int = Field(default=8000, description="API port")

    # Loki
    LOKI_URL: str = Field(default="http://localhost:3100", description="Loki API base URL")
    LOKI_TIMEOUT_SECONDS: int = Field(default=30, description="Loki query timeout")
    LOKI_MAX_LINES: int = Field(default=1000, description="Max log lines to retrieve")
    LOKI_LOOKBACK_MINUTES: int = Field(default=60, description="How far back to query logs")
    SLOW_QUERY_THRESHOLD_MS: int = Field(default=1000, description="Threshold for slow query detection")

    # Git
    GIT_REPOS_ROOT: str = Field(default="./repos", description="Root directory for git repositories")
    GIT_LOOKBACK_DAYS: int = Field(default=7, description="How many days of git history to check")
    HIGH_CHURN_COMMIT_COUNT: int = Field(default=5, description="Threshold for high-churn file detection")
    MAX_DIFF_LINES: int = Field(default=500, description="Max lines of diff to process per commit")

    # Jira
    JIRA_URL: str = Field(default="https://jira.example.com", description="Jira instance URL")
    JIRA_USERNAME: str = Field(default="", description="Jira username")
    JIRA_API_TOKEN: str = Field(default="", description="Jira API token")
    JIRA_TIMEOUT_SECONDS: int = Field(default=10, description="Jira API timeout")
    JIRA_MAX_CONCURRENT_REQUESTS: int = Field(default=10, description="Max concurrent Jira requests")

    # Reasoning engine
    CONFIDENCE_THRESHOLD: float = Field(default=85.0, description="Stop investigation when confidence > this")
    MAX_INVESTIGATION_STEPS: int = Field(default=10, description="Max reasoning steps before forced stop")

    # Report output
    REPORT_OUTPUT_DIR: str = Field(default="./reports", description="Where to write RCA reports")

    # Circuit breaker
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=5, description="Failures before circuit opens")
    CIRCUIT_BREAKER_TIMEOUT_SECONDS: int = Field(default=60, description="How long circuit stays open")

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
