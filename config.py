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

    # LLM Integration (Optional - for intelligent fallback)
    LLM_ENABLED: bool = Field(default=False, description="Enable LLM-enhanced analysis")
    LLM_PROVIDER: str = Field(default="anthropic", description="LLM provider (anthropic, openai, custom, mock)")
    LLM_API_KEY: str = Field(default="", description="LLM API key / Bearer token")
    LLM_MODEL: str = Field(default="claude-3-5-sonnet-20241022", description="LLM model to use")
    LLM_MAX_TOKENS: int = Field(default=2048, description="Max tokens per LLM request")
    LLM_TEMPERATURE: float = Field(default=0.0, description="LLM temperature (0=deterministic)")
    LLM_TIMEOUT: int = Field(default=30, description="LLM request timeout in seconds")
    LLM_CONFIDENCE_THRESHOLD: float = Field(default=40.0, description="Use LLM if pattern confidence < this")
    # Custom / internal provider
    LLM_BASE_URL: str = Field(default="", description="Base URL for custom/internal LLM (e.g. http://internal-llm.company.com/v1)")

    # Alert deduplication
    DEDUP_WINDOW_MINUTES: int = Field(default=30, description="Suppress duplicate alerts within this window (minutes)")
    DEDUP_ALLOW_RETRY_ON_FAILURE: bool = Field(default=True, description="Allow re-investigation when previous attempt failed")

    # Remediation
    AUTO_REMEDIATION_ENABLED: bool = Field(default=True, description="Auto-trigger fix workflow after RCA")
    AUTO_REMEDIATION_MIN_CONFIDENCE: str = Field(default="High", description="Min confidence level to auto-remediate: 'High' or 'Confirmed'")
    REMEDIATION_BRANCH_PREFIX: str = Field(default="fix/rca", description="Git branch prefix for fix branches")
    REMEDIATION_REMOTE: str = Field(default="origin", description="Git remote to push fix branches to")
    REMEDIATION_TEST_TIMEOUT_SECONDS: int = Field(default=300, description="Max seconds to wait for tests")
    REMEDIATION_MAX_FIX_ITERATIONS: int = Field(default=5, description="Max Claude agent iterations for code patch")

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
