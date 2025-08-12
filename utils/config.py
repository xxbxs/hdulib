from pathlib import Path

import toml
from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    """应用配置"""

    # API URLs
    url: dict[str, str] = {}

    # Headers
    init_headers: dict[str, str] = {}

    # Room and floor mappings
    room_name_dict: dict[str, str] = {}
    floor_name_dict: dict[str, str] = {}
    state_dict: dict[str, str] = {}

    # Default values
    org_id: str = "104"
    library_id: str = "104"

    # Optional fields that might be in config file
    title: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 忽略额外字段，避免配置文件中的额外字段导致错误
    }

    @property
    def login_url(self) -> str:
        return self.url.get("LOGIN_URL", "")

    @property
    def seat_state_url(self) -> str:
        return self.url.get("SEAT_CHECK_STATE_URL", "")

    @property
    def category_list_url(self) -> str:
        return self.url.get("CATEGORY_LIST_URL", "")

    @property
    def search_seats_url(self) -> str:
        return self.url.get("SEARCH_SEATS_URL", "")

    @property
    def reserve_seat_url(self) -> str:
        return self.url.get("RESERVE_SEAT_URL", "")

    @property
    def base_url(self) -> str:
        return self.url.get("BASE_URL", "")


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path or "./utils/config.toml"
        self._config: AppConfig | None = None

    @property
    def config(self) -> AppConfig:
        """获取配置实例"""
        if self._config is None:
            self._config = self.load_config()
        return self._config

    def load_config(self) -> AppConfig:
        """加载配置文件"""
        try:
            if Path(self.config_path).exists():
                config_data = toml.load(self.config_path)
                return AppConfig(**config_data)
            else:
                return AppConfig()
        except Exception as e:
            raise RuntimeError(f"Failed to load config: {e}")

    def reload_config(self) -> None:
        """重新加载配置"""
        self._config = None
