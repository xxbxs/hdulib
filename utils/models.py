from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import toml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    """应用配置"""

    # API URLs
    url: Dict[str, str] = {}

    # Headers
    init_headers: Dict[str, str] = {}

    # Room and floor mappings
    room_name_dict: Dict[str, str] = {}
    floor_name_dict: Dict[str, str] = {}
    state_dict: Dict[str, str] = {}

    # Default values
    org_id: str = "104"
    library_id: str = "104"

    # Optional fields that might be in config file
    title: Optional[str] = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 忽略额外字段，避免配置文件中的额外字段导致错误
    }

    @property
    def login_url(self) -> str:
        return self.url.get("LOGIN_URL", "")

    @property
    def seat_check_state_url(self) -> str:
        return self.url.get("SEAT_CHECK_STATE_URL", "")

    @property
    def seat_state_url(self) -> str:
        return self.url.get("SEAT_STATE_URL", "")

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

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "./utils/config.toml"
        self._config: Optional[AppConfig] = None

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


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class BookingTask(BaseModel):
    """预订任务模型"""

    user_name: str = Field(..., min_length=1, description="用户名")
    password: str = Field(..., min_length=1, description="密码")
    floor_id: str = Field(..., description="楼层ID")
    seat_number: str = Field(..., description="座位号")
    begin_time: int = Field(..., description="开始时间（小时或时间戳）")
    duration: int = Field(..., ge=1, le=12, description="持续时间（小时）")
    max_trials: int = Field(default=3, ge=1, le=100, description="最大尝试次数")
    interval: int = Field(default=2, ge=1, le=60, description="重试间隔（秒）")

    @field_validator("begin_time")
    @classmethod
    def validate_begin_time(cls, v):
        # 允许小时格式(0-23) 或时间戳格式(大于1000000000)
        if not ((0 <= v <= 23) or v > 1000000000):
            raise ValueError(
                "begin_time must be between 0-23 (hours) or a valid timestamp"
            )
        return v

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, v):
        if not (1 <= v <= 12):
            raise ValueError("duration must be between 1-12 hours")
        return v

    @property
    def days_ahead(self) -> int:
        """获取需要提前预订的天数"""
        # 转换为整数进行比较
        floor_id_int = int(self.floor_id) if self.floor_id.isdigit() else 0
        return 1 if floor_id_int in [1547, 1548] else 2

    @property
    def max_duration_per_task(self) -> int:
        """获取单次任务最大持续时间"""
        floor_id_int = int(self.floor_id) if self.floor_id.isdigit() else 0
        if floor_id_int in [1547, 1548]:
            return min(4, self.duration)  # 确保不超过4小时
        return self.duration


class BookingResult(BaseModel):
    """预订结果模型"""

    success: bool
    user: str
    seat_info: str
    booking_time: Optional[str] = None
    duration: Optional[str] = None
    attempt: Optional[int] = None
    attempts: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None


class SeatInfo(BaseModel):
    """座位信息模型"""

    seat_id: int
    floor_id: str
    seat_number: str
    room_name: Optional[str] = None
    floor_name: Optional[str] = None
