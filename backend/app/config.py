"""Application configuration, loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    app_name: str = "Contract Signing App"
    api_prefix: str = "/api"
    debug: bool = False

    # Database
    database_url: str = "postgresql+psycopg2://contract_app:contract_app@localhost:5432/contracts"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # Object storage. "local" writes to storage_dir; "s3" talks to MinIO or S3.
    storage_backend: str = "s3"
    storage_dir: str = "/data/storage"

    # Object storage (MinIO / S3)
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "contracts"
    minio_secure: bool = False
    presigned_url_ttl_seconds: int = 300

    # Auth
    jwt_secret: str = "change-me-in-production"
    # Separate key for encrypting stored PII. Falls back to jwt_secret when unset.
    pii_encryption_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 8

    # Uploads
    max_upload_bytes: int = 50 * 1024 * 1024

    # Login throttling
    login_max_attempts: int = 8
    login_lockout_seconds: int = 900

    # Share-for-fill links (EDITOR-V2-PLAN §8, §11 Q4: LAN only, fill only).
    # The link handed out is `<share_base_url>/s#t=<token>` -- the token rides
    # in the fragment, which a browser never sends in a request or a Referer.
    share_base_url: str = "http://10.31.0.58:5173"
    # Public share endpoints answer only a peer address inside these networks
    # (`request.client.host`; X-Forwarded-For is deliberately ignored).
    share_allowed_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8"
    share_default_ttl_hours: int = 72
    share_max_ttl_hours: int = 14 * 24
    # Failed token lookups per source address before 429.
    share_fail_limit: int = 20
    share_fail_window_seconds: int = 600

    # Gallery images deleted by their owner (soft delete, `assets.deleted_at`)
    # are purged -- row and any leftover bytes -- this many days later by
    # `scripts/purge_deleted_assets.py`. Contract copies are never touched.
    deleted_asset_retention_days: int = 30

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def s3_endpoint_url(self) -> str:
        scheme = "https" if self.minio_secure else "http"
        return f"{scheme}://{self.minio_endpoint}"


DEFAULT_SECRETS = {"change-me-in-production", "change-me-to-a-long-random-string", ""}


@lru_cache
def get_settings() -> Settings:
    loaded = Settings()
    # Refuse to serve real traffic signed with the example secret. In debug the
    # default is a convenience; outside it, it is a silent authentication hole.
    if not loaded.debug and loaded.jwt_secret in DEFAULT_SECRETS:
        raise RuntimeError(
            "JWT_SECRET is unset or still the example value. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return loaded


settings = get_settings()
