"""Settings from environment variables."""

from pydantic import AnyHttpUrl, Field, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, env_file=".env", extra="ignore")

    database_url: str
    secret_key: str = Field(min_length=32)
    db_pool_min: PositiveInt = 1
    db_pool_max: PositiveInt = 8
    ingest_max_compressed_bytes: PositiveInt = 25 * 1024 * 1024
    ingest_max_json_bytes: PositiveInt = 200 * 1024 * 1024
    retain_detail_publications: PositiveInt = 2
    retain_dumps_recent: PositiveInt = 30
    retain_dumps_monthly: bool = True
    abandon_open_runs_after_hours: PositiveInt = 48
    smtp_host: str | None = None
    smtp_port: PositiveInt = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    mail_from: str | None = None
    webmaster_email: str | None = None
    contact_limit_per_hour: PositiveInt = 5
    site_url: AnyHttpUrl | None = None
    login_link_minutes: PositiveInt = 15
    login_limit_per_email: PositiveInt = 3
    login_limit_per_ip_per_hour: PositiveInt = 20
    session_days: PositiveInt = 30
    api_requests_per_minute: PositiveInt = 60
    api_keys_per_user: PositiveInt = 5
    secure_cookies: bool = True

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and self.mail_from)

    @property
    def contact_configured(self) -> bool:
        return self.mail_configured and bool(self.webmaster_email)

    @property
    def links_configured(self) -> bool:
        """Sign-in links need mail and SITE_URL, so links never take their host from a request."""
        return self.mail_configured and self.site_url is not None

    def site_link(self, path: str) -> str:
        """Absolute URL for a site path, for use in emails."""
        if self.site_url is None:
            raise ValueError("SITE_URL is not set")
        return str(self.site_url).rstrip("/") + path
