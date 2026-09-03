from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPMT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mpmt:mpmt@localhost:5432/mpmt"
    wb_token_file: str = "secrets/WBtoken.txt"
    domain: str = "gis.adel-factory.ru"
    mt_base_v3: str = "https://markirovka.crpt.ru/api/v3/true-api"
    mt_base_v4: str = "https://markirovka.crpt.ru/api/v4/true-api"
    mt_pg: str = "lp"
    mt_inn: str = "090201471350"
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    poll_excise_cron: list[str] = ["06:30", "18:30"]   # МСК, ровно 2 запроса/24ч
    excise_days_back: int = 7

settings = Settings()
