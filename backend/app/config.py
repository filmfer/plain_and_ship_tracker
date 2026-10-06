"""Application configuration.

Centralised, environment-driven settings.

Design notes
------------
* ``APP_MODE`` defaults to ``demo`` so the application can start with no ``.env``
  file, no credentials and no internet access.
* ``env_file`` is optional; if ``.env`` is missing the defaults still apply.
* All provider credentials stay server-side and are never sent to the frontend.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMode(str, Enum):
    """Supported application modes."""

    demo = "demo"
    local = "local"
    production = "production"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Plain Boat Radar"
    app_version: str = "0.1.0"

    # Defaults to demo when unset -> offline, deterministic, no credentials.
    app_mode: AppMode = AppMode.demo

    # HTTP server binding (used by the CLI entrypoint in later phases).
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Comma-separated allowed CORS origins for the frontend.
    cors_origins: str = "http://localhost:5173"

    # Deterministic mock/simulation controls (consumed from Phase 2 onward).
    mock_seed: int = 42
    mock_time_multiplier: float = 1.0
    # Real seconds between simulation ticks (loop pacing).
    mock_tick_interval: float = 1.0
    # Deterministic mock history (trails): a small, bounded sample of past positions.
    history_sample_count: int = 20
    history_sample_interval_s: float = 60.0
    history_max_points: int = 200

    # --- Current-state cache (Redis) ---------------------------------------
    # The latest entity/position state is mirrored here; PostgreSQL is not
    # involved (cache only, never history).
    redis_url: str = "redis://localhost:6379/0"
    # Kill-switch: false -> pure in-memory cache, no Redis connection attempted.
    state_cache_enabled: bool = True
    # Fail fast so demo/local startup never blocks on a missing Redis.
    redis_timeout_s: float = 1.0
    # Cached state expires so a stopped instance never leaves stale state forever.
    redis_ttl_s: int = 3600

    # --- Historical storage (PostgreSQL/PostGIS) ----------------------------
    # Empty (demo default) -> no database required; history stays in memory.
    # Example: postgresql://postgres@localhost:5432/plain_boat_radar
    database_url: str = ""
    # Fail fast so startup/migrations never hang on an unreachable server.
    db_connect_timeout_s: int = 2

    # --- Historical write thresholds (meaningful-change filter) -------------
    # A position is persisted only when it is "meaningful" versus that entity's
    # last *written* position; see app.history_write.HistoryWriteService.
    # Units: metres / seconds / same units as Position.speed / degrees.
    min_position_change_meters: float = 50.0
    min_time_between_history_writes: float = 60.0
    min_speed_change: float = 5.0
    min_heading_change: float = 10.0

    # --- Historical API (GET /api/history/{entity_id}) ----------------------
    # Size protections so one request can never return an unbounded dataset:
    # default page size, hard cap (also the database fetch window), and even
    # downsampling when a track holds more points than the requested limit.
    history_api_default_limit: int = 500
    history_api_max_points: int = 2000

    # --- OpenSky provider (APP_MODE=production only; never used in demo) -----
    # Explicit kill-switch. The provider is only constructed when ALL hold:
    # OPENSKY_ENABLED=true AND APP_MODE=production AND client credentials are
    # set. demo/local never construct it, whatever this flag says.
    opensky_enabled: bool = False
    # OAuth2 client-credentials flow (basic auth was removed by OpenSky).
    # Create a client at https://opensky-network.org/my-opensky/account.
    # Environment/.env only - never hard-coded, never logged, never sent to
    # the frontend (rules #30/#32).
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    opensky_base_url: str = "https://opensky-network.org/api"
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/"
        "protocol/openid-connect/token"
    )
    # Client-side throttle between /states/all calls. 0 = documented floors of
    # the official client: 5s authenticated / 10s anonymous (never lowered).
    opensky_min_request_interval_s: float = 0.0
    # Serve cached states this long before considering a refresh.
    opensky_cache_ttl_s: float = 5.0
    opensky_timeout_s: float = 10.0
    # Retries for transient failures with exponential backoff. HTTP 429 never
    # sleeps: it arms a cooldown from X-Rate-Limit-Retry-After-Seconds instead.
    opensky_max_retries: int = 2
    opensky_backoff_base_s: float = 1.0
    # Optional WGS84 bounding box "lamin,lamax,lomin,lomax" (comma-separated).
    # Empty = global query (4 credits/request per the documented credit table).
    opensky_bbox: str = ""

    # --- AISHub AIS provider (APP_MODE=production only; never used in demo) ---
    # Contributor-based access: a username is only issued to members streaming
    # their own raw AIS feed (Terms of Use on aishub.net/join-us; synthesized
    # or scraped feeds are prohibited). Free for approved contributors;
    # without one the ships keep using mock/local data.
    aishub_enabled: bool = False
    # Membership username (the documented webservice credential).
    # Environment/.env only - never hard-coded, never logged.
    aishub_username: str = ""
    aishub_base_url: str = "https://data.aishub.net/ws.php"
    # Documented hard floor: "Don't access the webservice more frequently than
    # once per minute!" - enforced client-side; settings can only raise it.
    aishub_min_request_interval_s: float = 60.0
    aishub_cache_ttl_s: float = 60.0
    aishub_timeout_s: float = 10.0
    aishub_max_retries: int = 2
    aishub_backoff_base_s: float = 1.0
    # Optional WGS84 bbox "lamin,lamax,lomin,lomax"; empty = worldwide.
    aishub_bbox: str = ""

    # --- Local AIS receiver (APP_MODE=local opt-in; never used in demo) ------
    # Explicit kill-switch. The provider is only constructed when BOTH hold:
    # LOCAL_AIS_ENABLED=true AND APP_MODE=local. demo always uses the mock,
    # whatever this flag says. No hardware is required: without a receiver
    # attached the provider serves empty snapshots.
    local_ais_enabled: bool = False
    # Serve receiver snapshots this long before polling the receiver again.
    local_ais_cache_ttl_s: float = 5.0

    # --- Tracking-data freshness (LIVE/RECENT/STALE/LOST/UNKNOWN) ------------
    # Data age (app-clock now minus latest position timestamp) is classified
    # against these ascending thresholds (seconds). These are tracking-data
    # states, never safety assessments.
    data_state_live_s: float = 10.0
    data_state_recent_s: float = 60.0
    data_state_stale_s: float = 900.0

    # --- Anomaly engine (provider-independent; thresholds in native units) ----
    # Stale position: no material movement for longer than this -> WATCH.
    # Canonical name per spec (default 15 minutes).
    anomaly_stale_minutes: float = 15.0
    # Deprecated alias (seconds); kept so ANOMALY_STALE_AFTER_S still works.
    # When both are explicitly set, minutes win; otherwise they sync.
    anomaly_stale_after_s: float = 900.0
    # Signal loss: no new data at all for longer than this -> WARNING.
    # Canonical value in minutes (rule #24/#25); seconds alias below.
    anomaly_signal_lost_minutes: float = 15.0
    anomaly_signal_lost_after_s: float = 900.0
    # Impossible jump between consecutive reports (great-circle metres).
    anomaly_max_jump_meters: float = 500000.0
    # Implausible reported speeds (same units as Position.speed, m/s).
    anomaly_max_aircraft_speed: float = 350.0
    anomaly_max_ship_speed: float = 35.0
    # Implausible acceleration (m/s^2).
    anomaly_max_aircraft_accel: float = 20.0
    anomaly_max_ship_accel: float = 2.0
    # Maximum sudden change in heading/course (degrees) between reports.
    anomaly_max_heading_change_deg: float = 90.0
    # Minimum movement (metres) before a heading change is considered valid
    # (prevents noise at low speeds/standstill).
    anomaly_min_move_for_heading_check_m: float = 100.0
    # Maximum plausible altitude (metres) for aircraft.
    anomaly_max_altitude_m: float = 20000.0
    # Maximum plausible vertical rate (metres/second) for aircraft.
    anomaly_max_vertical_rate_mps: float = 50.0

    # Anomaly scoring weights (multiplier for each finding's 0-100 score).
    anomaly_weight_position_stale: float = 1.0
    anomaly_weight_signal_lost: float = 2.0
    anomaly_weight_position_jump: float = 1.5
    anomaly_weight_speed_inconsistent: float = 1.0
    anomaly_weight_heading_inconsistent: float = 0.5
    anomaly_weight_altitude_inconsistent: float = 0.5


    @model_validator(mode="after")
    def _sync_anomaly_thresholds(self) -> Settings:
        """Keep ``minutes`` / ``_after_s`` fields in sync."""
        # Sync Stale
        minutes_default = Settings.model_fields["anomaly_stale_minutes"].default
        seconds_default = Settings.model_fields["anomaly_stale_after_s"].default
        minutes_set = self.anomaly_stale_minutes != minutes_default
        seconds_set = self.anomaly_stale_after_s != seconds_default
        if minutes_set or not seconds_set:
            self.anomaly_stale_after_s = self.anomaly_stale_minutes * 60.0
        else:
            self.anomaly_stale_minutes = self.anomaly_stale_after_s / 60.0

        # Sync Signal Loss
        minutes_default = Settings.model_fields["anomaly_signal_lost_minutes"].default
        seconds_default = Settings.model_fields["anomaly_signal_lost_after_s"].default
        minutes_set = self.anomaly_signal_lost_minutes != minutes_default
        seconds_set = self.anomaly_signal_lost_after_s != seconds_default
        if minutes_set or not seconds_set:
            self.anomaly_signal_lost_after_s = self.anomaly_signal_lost_minutes * 60.0
        else:
            self.anomaly_signal_lost_minutes = self.anomaly_signal_lost_after_s / 60.0
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins as a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
