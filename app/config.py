"""应用配置。所有密钥只从环境变量 / .env 读取，键名见 .env.example。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_model: str = "claude-opus-5-5"
    anthropic_api_key: str = ""

    # 飞书
    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_bitable_app_token: str = ""
    feishu_bitable_table_id: str = ""

    # Google Cloud Vision
    google_application_credentials: str = ""
    google_cloud_project: str = ""

    # ACRCloud
    acrcloud_host: str = ""
    acrcloud_access_key: str = ""
    acrcloud_access_secret: str = ""

    # AWS Rekognition
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # 可选服务（P1/P2）
    sightengine_api_user: str = ""
    sightengine_api_secret: str = ""
    hive_api_key: str = ""
    whatfontis_api_key: str = ""
    ngrok_authtoken: str = ""

    # 应用
    app_hmac_secret: str = "dev-only-change-me"
    demo_mode: bool = False
    database_url: str = "sqlite:///./data/adsentry.db"
    data_dir: Path = Path("./data")
    fixtures_dir: Path = Path("./fixtures")
    max_upload_mb: int = 200
    budget_per_asset_usd: float = 0.25
    review_token_ttl_hours: int = 24

    # 外部工具超时（秒），PROMPT.md §7
    timeout_image_search: float = 8.0
    timeout_music: float = 10.0
    timeout_celebrity: float = 8.0
    timeout_llm: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
