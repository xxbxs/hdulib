import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from utils.booking_service import BookingService
from utils.config import ConfigManager
from utils.console import console, logger
from utils.models import BookingResult, BookingTask


def parse_config_string(config: str) -> list[BookingTask]:
    """解析配置字符串"""
    if not config.strip():
        raise ValueError("Configuration is empty")

    user_configs = re.split(r"---", config)
    tasks = []
    booking_service = BookingService(ConfigManager())

    for i, user_config in enumerate(user_configs):
        if not user_config.strip():
            continue

        try:
            config_dict = dict(re.findall(r"(\w+)\s*=\s*(\S+)", user_config.strip()))
            if not config_dict:
                console.warning(f"Empty configuration block {i + 1}")
                continue

            user_tasks = booking_service.create_tasks_from_config(config_dict)
            tasks.extend(user_tasks)

        except Exception as e:
            console.error(f"Error in configuration block {i + 1}: {e}")
            continue

    return tasks


def display_task_summary(tasks: list[BookingTask]) -> None:
    """显示任务摘要"""
    if not tasks:
        console.warning("No tasks to display")
        return

    console.info(f"Task Summary ({len(tasks)} total tasks)")
    print("=" * 90)

    # 表头
    headers = [
        "User",
        "Floor",
        "Seat",
        "Booking Time",
        "Duration",
        "Trials",
        "Interval",
    ]
    widths = [12, 8, 6, 18, 10, 8, 10]

    # 打印表头
    header_line = "".join(h.ljust(w) for h, w in zip(headers, widths))
    from utils.console import Colors

    console.print(header_line, Colors.BLUE + Colors.BOLD)
    print("-" * 90)

    # 打印数据行
    for task in tasks:
        begin_dt = datetime.fromtimestamp(task.begin_time)
        row = [
            str(task.user_name)[:11],
            str(task.floor_id)[:7],
            str(task.seat_number)[:5],
            begin_dt.strftime("%Y-%m-%d %H:%M")[:17],
            f"{task.duration}h"[:9],
            str(task.max_trials)[:7],
            f"{task.interval}s"[:9],
        ]
        row_line = "".join(cell.ljust(w) for cell, w in zip(row, widths))
        print(row_line)

    print("=" * 90)


def display_results(results: list[BookingResult]) -> None:
    """显示预订结果"""
    if not results:
        console.warning("No results to display")
        return

    console.info("Booking Results Summary")
    print("=" * 100)

    # 表头
    headers = ["User", "Seat Info", "Status", "Time", "Duration", "Attempts", "Details"]
    widths = [12, 20, 10, 18, 10, 10, 25]

    # 打印表头
    from utils.console import Colors

    header_line = "".join(h.ljust(w) for h, w in zip(headers, widths))
    console.print(header_line, Colors.GREEN + Colors.BOLD)
    print("-" * 100)

    # 打印数据行
    for result in results:
        status = "Success" if result.success else "Failed"
        booking_time = result.booking_time or "N/A"
        duration = result.duration or "N/A"
        attempts = str(result.attempt or result.attempts or "N/A")

        details = result.message if result.success else result.error or "Unknown error"
        if details and len(str(details)) > 22:
            details = str(details)[:19] + "..."

        row = [
            str(result.user)[:11],
            str(result.seat_info)[:19],
            status[:9],
            booking_time[:17],
            duration[:9],
            attempts[:9],
            str(details)[:24],
        ]

        row_line = "".join(cell.ljust(w) for cell, w in zip(row, widths))

        # 根据状态着色
        if result.success:
            console.print(row_line, Colors.GREEN)
        else:
            console.print(row_line, Colors.RED)

    print("=" * 100)


def book_command():
    """执行预订命令"""
    console.header("HDU Library Seat Booking System")

    try:
        config_content = os.environ.get("CONFIG", "")
        # config_file = Path("a.txt")

        # if config_file.exists():
        #     config_content = config_file.read_text(encoding="utf-8")

        if not config_content:
            console.error("No CONFIG environment variable found")
            sys.exit(1)

        console.info("Parsing configuration...")
        tasks = parse_config_string(config_content)

        if not tasks:
            console.warning("No valid tasks found in configuration")
            sys.exit(1)

        console.success(f"Found {len(tasks)} booking task(s)")

        # 显示任务摘要
        display_task_summary(tasks)

        # 执行预订
        console.info("Starting booking process...")

        config_manager = ConfigManager()
        booking_service = BookingService(config_manager)

        results = asyncio.run(booking_service.run_multiple_tasks(tasks))

        # 显示结果
        display_results(results)

        # 统计结果
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful

        if successful > 0:
            console.success(f"Summary: {successful} successful, {failed} failed")
        else:
            console.error(f"Summary: {successful} successful, {failed} failed")

    except Exception as e:
        import traceback

        console.error(f"Unexpected error: {e}")
        console.error(f"Traceback: {traceback.format_exc()}")
        logger.error(f"Booking command error: {e}")
        sys.exit(1)


def validate_command(config_file: Path):
    """验证配置文件"""
    if not config_file.exists():
        console.error(f"Configuration file {config_file} not found")
        sys.exit(1)

    try:
        config_content = config_file.read_text(encoding="utf-8")
        tasks = parse_config_string(config_content)

        console.success(f"Configuration is valid! Found {len(tasks)} task(s)")

        # 显示简单摘要
        for i, task in enumerate(tasks, 1):
            begin_dt = datetime.fromtimestamp(task.begin_time)
            console.info(
                f"  {i}. {task.user_name} - Floor {task.floor_id}, "
                f"Seat {task.seat_number} at {begin_dt.strftime('%Y-%m-%d %H:%M')}"
            )

    except Exception as e:
        console.error(f"Configuration error: {e}")
        sys.exit(1)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="HDU Library Seat Booking System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # book 子命令
    subparsers.add_parser("book", help="Book library seats based on configuration")

    # validate 子命令
    validate_parser = subparsers.add_parser(
        "validate", help="Validate configuration file"
    )
    validate_parser.add_argument(
        "config_file", type=Path, help="Configuration file to validate"
    )

    args = parser.parse_args()

    if args.command == "book":
        book_command()
    elif args.command == "validate":
        validate_command(args.config_file)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
