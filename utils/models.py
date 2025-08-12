from enum import StrEnum

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
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
    booking_time: str | None = None
    duration: str | None = None
    attempt: int | None = None
    attempts: int | None = None
    message: str | None = None
    error: str | None = None


class SeatInfo(BaseModel):
    """座位信息模型"""

    seat_id: int
    floor_id: str
    seat_number: str
    room_name: str | None = None
    floor_name: str | None = None
