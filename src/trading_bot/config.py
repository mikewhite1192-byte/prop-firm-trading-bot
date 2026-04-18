from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql+psycopg://trading_bot:trading_bot@localhost:5432/trading_bot"
    )
    log_level: str = "INFO"
    timezone: str = "America/New_York"

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_account_id: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    oanda_api_token: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"

    tradovate_username: str = ""
    tradovate_password: str = ""
    tradovate_app_id: str = ""
    tradovate_app_version: str = "0.1.0"
    tradovate_client_id: str = ""
    tradovate_client_secret: str = ""
    tradovate_environment: str = "demo"

    rithmic_username: str = ""
    rithmic_password: str = ""
    rithmic_system_name: str = ""
    rithmic_cert_path: str = ""

    mt5_account: str = ""
    mt5_password: str = ""
    mt5_server: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
