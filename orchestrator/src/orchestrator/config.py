from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPOMETA_", extra="ignore")

    database_url: str
    api_key: str

    scaleway_region: str = "fr-par"
    scaleway_project_id: str
    scaleway_access_key: str
    scaleway_secret_key: str

    s3_endpoint: str = "https://s3.fr-par.scw.cloud"
    s3_bucket: str = "pipometa"

    secret_oauth_token_id: str
    """Scaleway Secret Manager secret ID holding CLAUDE_CODE_OAUTH_TOKEN."""

    worker_image: str
    """Full image URI of the worker, e.g. rg.fr-par.scw.cloud/nova-container-registry/pipometa-worker:latest"""

    public_url: str
    """Externally-reachable URL of this orchestrator, used by workers for callbacks."""

    dispatch_concurrency: int = 1
    heartbeat_stale_seconds: int = 90

    cron_secret: str = ""
    """Shared secret carried in the cron-trigger body. If empty, cron tick is open (dev only)."""


settings = Settings()  # type: ignore[call-arg]
