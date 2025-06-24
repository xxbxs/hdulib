import asyncio
from datetime import datetime, timedelta
from typing import List

from loguru import logger

from utils.api_client import LibraryAPIClient
from utils.config import ConfigManager
from utils.models import BookingResult, BookingTask


class BookingService:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def create_tasks_from_config(self, config_dict: dict) -> List[BookingTask]:
        """从配置字典创建任务"""
        # 验证必需字段
        required_fields = [
            "user_name",
            "password",
            "floor_id",
            "seat_number",
            "begin_time",
            "duration",
        ]
        missing_fields = [
            field for field in required_fields if not config_dict.get(field)
        ]

        if missing_fields:
            raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")

        # 验证和转换数据类型
        try:
            config_dict["begin_time"] = int(config_dict["begin_time"])
            config_dict["duration"] = int(config_dict["duration"])
            config_dict["max_trials"] = int(config_dict.get("max_trials", 3))
            config_dict["interval"] = int(config_dict.get("interval", 2))
        except ValueError as e:
            raise ValueError(f"Invalid numeric values in config: {e}")

        # 创建基础任务
        base_task = BookingTask(**config_dict)

        # 分割长时间任务
        return self._split_long_duration_task(base_task)

    def _split_long_duration_task(self, task: BookingTask) -> List[BookingTask]:
        """分割长时间任务"""
        tasks = []

        # 确定开始时间是小时格式还是时间戳格式
        if int(task.begin_time) <= 23:
            start_time = datetime.now() + timedelta(days=task.days_ahead)
            current_begin_hour = task.begin_time
            remaining_duration = task.duration

            while int(remaining_duration) > 0:
                # 计算这次任务的持续时间
                task_duration = min(remaining_duration, task.max_duration_per_task)

                # 防止无限循环
                if int(task_duration) <= 0:
                    logger.error(
                        "Task duration is 0, breaking to prevent infinite loop"
                    )
                    break

                # 计算预订时间戳
                booking_datetime = start_time.replace(
                    hour=current_begin_hour % 24, minute=0, second=0, microsecond=0
                )

                # 处理跨天情况
                if current_begin_hour >= 24:
                    days_to_add = current_begin_hour // 24
                    booking_datetime += timedelta(days=days_to_add)

                # 创建子任务
                sub_task = task.model_copy()  # 使用 model_copy 替代 copy
                sub_task.begin_time = int(booking_datetime.timestamp())
                sub_task.duration = task_duration

                tasks.append(sub_task)

                remaining_duration -= task_duration
                current_begin_hour += task_duration

        else:  # 时间戳格式
            # 如果已经是时间戳，只需要分割持续时间
            current_timestamp = task.begin_time
            remaining_duration = task.duration

            while remaining_duration > 0:
                task_duration = min(remaining_duration, task.max_duration_per_task)

                if task_duration <= 0:
                    logger.error(
                        "Task duration is 0, breaking to prevent infinite loop"
                    )
                    break

                sub_task = task.model_copy()
                sub_task.begin_time = current_timestamp
                sub_task.duration = task_duration

                tasks.append(sub_task)

                remaining_duration -= task_duration
                current_timestamp += task_duration * 3600  # 增加对应的秒数

        if not tasks:
            logger.warning("No tasks created, using original task")
            return [task]

        logger.info(f"Split task into {len(tasks)} sub-tasks")
        return tasks

    async def run_booking_task(self, task: BookingTask) -> BookingResult:
        """执行单个预订任务"""
        logger.info(f"Starting booking task for {task.user_name}")

        try:
            async with LibraryAPIClient(self.config_manager) as client:
                # 登录
                uid = await client.login(task.user_name, task.password)
                if not uid:
                    return BookingResult(
                        success=False,
                        user=task.user_name,
                        seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                        error="Login failed",
                    )

                # 获取座位ID
                seat_id = await client.get_seat_id(task.floor_id, task.seat_number)
                if seat_id == 0:
                    return BookingResult(
                        success=False,
                        user=task.user_name,
                        seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                        error="Seat not found or invalid seat number",
                    )

                # 等待预订窗口
                await self._wait_for_booking_window(task)

                # 执行预订尝试
                return await self._attempt_booking(client, task, seat_id)

        except asyncio.CancelledError:
            logger.warning(f"Booking task cancelled for {task.user_name}")
            return BookingResult(
                success=False,
                user=task.user_name,
                seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                error="Task cancelled",
            )
        except Exception as e:
            logger.error(f"Unexpected error for {task.user_name}: {e}")
            return BookingResult(
                success=False,
                user=task.user_name,
                seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                error=f"Unexpected error: {str(e)}",
            )

    async def _wait_for_booking_window(self, task: BookingTask) -> None:
        """等待预订窗口开放"""
        try:
            current_time = datetime.now()
            begin_datetime = datetime.fromtimestamp(task.begin_time)

            # 计算预订窗口开放时间
            booking_opens_day = begin_datetime - timedelta(days=task.days_ahead)
            booking_opens_time = booking_opens_day.replace(
                hour=20, minute=0, second=0, microsecond=0
            )

            if current_time < booking_opens_time:
                wait_seconds = (booking_opens_time - current_time).total_seconds()
                if wait_seconds > 0:
                    logger.info(
                        f"Waiting until {booking_opens_time.strftime('%Y-%m-%d %H:%M')} "
                        f"for {task.user_name} (waiting {wait_seconds:.0f} seconds)"
                    )
                    await asyncio.sleep(wait_seconds)
            else:
                logger.info(f"Booking window already open for {task.user_name}")

        except Exception as e:
            logger.error(f"Error in wait_for_booking_window: {e}")
            # 继续执行，不因为等待时间计算错误而中断

    async def _attempt_booking(
        self, client: LibraryAPIClient, task: BookingTask, seat_id: int
    ) -> BookingResult:
        """尝试预订座位"""
        logger.info(f"Starting booking attempts for {task.user_name}")

        last_error_message = "Unknown error"
        successful_attempt = 0

        for attempt in range(task.max_trials):
            logger.info(f"Attempt {attempt + 1}/{task.max_trials} for {task.user_name}")

            try:
                result = await client.confirm_seat(
                    task.begin_time, task.duration, seat_id
                )

                if result == "ok":
                    successful_attempt = attempt + 1
                    begin_datetime = datetime.fromtimestamp(task.begin_time)

                    logger.success(
                        f"Booking successful for {task.user_name} on attempt {successful_attempt}"
                    )

                    return BookingResult(
                        success=True,
                        user=task.user_name,
                        seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                        booking_time=begin_datetime.strftime("%Y-%m-%d %H:%M"),
                        duration=f"{task.duration}h",
                        attempt=successful_attempt,
                        message="Seat reservation successful",
                    )
                else:
                    last_error_message = result
                    logger.warning(
                        f"Attempt {attempt + 1} failed for {task.user_name}: {last_error_message}"
                    )

            except asyncio.CancelledError:
                logger.warning(f"Booking attempt cancelled for {task.user_name}")
                raise
            except Exception as e:
                last_error_message = f"Request error: {str(e)}"
                logger.error(
                    f"Request error on attempt {attempt + 1} for {task.user_name}: {e}"
                )

            # 等待重试间隔（除了最后一次尝试）
            if attempt < task.max_trials - 1:
                logger.debug(
                    f"Waiting {task.interval}s before next attempt for {task.user_name}"
                )
                await asyncio.sleep(task.interval)

        # 所有尝试都失败
        error_msg = f"Failed after {task.max_trials} attempts: {last_error_message}"
        logger.error(f"All booking attempts failed for {task.user_name}: {error_msg}")

        return BookingResult(
            success=False,
            user=task.user_name,
            seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
            error=error_msg,
            attempts=task.max_trials,
        )

    async def run_multiple_tasks(self, tasks: List[BookingTask]) -> List[BookingResult]:
        """并发执行多个预订任务"""
        if not tasks:
            logger.warning("No tasks provided")
            return []

        logger.info(f"Starting {len(tasks)} booking tasks...")

        # 限制并发数量，避免过多并发请求
        max_concurrent = min(10, len(tasks))
        semaphore = asyncio.Semaphore(max_concurrent)

        async def bounded_task(task: BookingTask):
            async with semaphore:
                return await self.run_booking_task(task)

        try:
            # 执行所有任务
            results = await asyncio.gather(
                *[bounded_task(task) for task in tasks], return_exceptions=True
            )

            # 处理异常结果
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    task = tasks[i]
                    logger.error(
                        f"Task execution failed for {task.user_name}: {result}"
                    )
                    processed_results.append(
                        BookingResult(
                            success=False,
                            user=task.user_name,
                            seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                            error=f"Task execution failed: {str(result)}",
                        )
                    )
                else:
                    processed_results.append(result)

            # 统计结果
            successful = sum(1 for r in processed_results if r.success)
            logger.info(
                f"Completed {len(processed_results)} tasks: {successful} successful, {len(processed_results) - successful} failed"
            )

            return processed_results

        except Exception as e:
            logger.error(f"Error in run_multiple_tasks: {e}")
            # 返回失败结果
            return [
                BookingResult(
                    success=False,
                    user=task.user_name,
                    seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                    error=f"Batch execution failed: {str(e)}",
                )
                for task in tasks
            ]

    async def run_booking_with_retry(
        self, task: BookingTask, global_max_retries: int = 3
    ) -> BookingResult:
        """运行预订任务，支持全局重试"""
        if global_max_retries < 1:
            logger.warning("global_max_retries must be at least 1, setting to 1")
            global_max_retries = 1

        last_result = None

        for global_attempt in range(global_max_retries):
            try:
                logger.info(
                    f"Global attempt {global_attempt + 1}/{global_max_retries} for {task.user_name}"
                )

                result = await self.run_booking_task(task)
                last_result = result

                if result.success:
                    logger.success(
                        f"Global retry successful for {task.user_name} on attempt {global_attempt + 1}"
                    )
                    return result

                # 如果失败且还有重试机会，等待一段时间
                if global_attempt < global_max_retries - 1:
                    wait_time = 2 * (global_attempt + 1)  # 递增等待时间
                    logger.info(
                        f"Global retry waiting {wait_time}s for {task.user_name}"
                    )
                    await asyncio.sleep(wait_time)

            except asyncio.CancelledError:
                logger.warning(f"Global retry cancelled for {task.user_name}")
                raise
            except Exception as e:
                logger.error(
                    f"Global attempt {global_attempt + 1} failed for {task.user_name}: {e}"
                )
                last_result = BookingResult(
                    success=False,
                    user=task.user_name,
                    seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                    error=f"Global attempt {global_attempt + 1} failed: {str(e)}",
                )

                if global_attempt == global_max_retries - 1:
                    break

        # 返回最后一次尝试的结果，或者创建一个失败结果
        if last_result is None:
            last_result = BookingResult(
                success=False,
                user=task.user_name,
                seat_info=f"Floor {task.floor_id}, Seat {task.seat_number}",
                error="All global attempts failed: No result available",
            )

        logger.error(f"All global retries failed for {task.user_name}")
        return last_result
