import re
from pathlib import Path
from typing import List

import typer
from rich.console import Console
from rich.table import Table

from utils.booking_service import BookingService
from utils.config import ConfigManager
from utils.models import BookingResult, BookingTask

console = Console()
app = typer.Typer(help="HDU Library Seat Booking System", rich_markup_mode="rich")


class ConfigParser:
    """配置解析器"""

    @staticmethod
    def parse_config_string(config: str) -> List[BookingTask]:
        """解析配置字符串"""
        if not config.strip():
            raise ValueError("Configuration is empty")

        user_configs = re.split(r"---", config)
        tasks = []

        for i, user_config in enumerate(user_configs):
            if not user_config.strip():
                continue

            try:
                config_dict = dict(
                    re.findall(r"(\w+)\s*=\s*(\S+)", user_config.strip())
                )
                if not config_dict:
                    console.print(
                        f"[yellow]⚠️  Warning: Empty configuration block {i + 1}[/yellow]"
                    )
                    continue

                booking_service = BookingService(ConfigManager())
                user_tasks = booking_service.create_tasks_from_config(config_dict)
                tasks.extend(user_tasks)

            except Exception as e:
                console.print(
                    f"[red]❌ Error in configuration block {i + 1}: {e}[/red]"
                )
                continue

        return tasks


class ResultsDisplayer:
    """结果显示器"""

    @staticmethod
    def display_task_summary(tasks: List[BookingTask]) -> None:
        """显示任务摘要"""
        if not tasks:
            console.print("[yellow]⚠️  No tasks to display[/yellow]")
            return

        table = Table(
            title=f"📋 Task Summary ({len(tasks)} total tasks)",
            show_header=True,
            header_style="bold cyan",
            border_style="blue",
        )

        table.add_column("👤 User", style="cyan", min_width=12)
        table.add_column("🏢 Floor", style="green", justify="center", min_width=6)
        table.add_column("💺 Seat", style="yellow", justify="center", min_width=6)
        table.add_column("⏰ Booking Time", style="blue", min_width=16)
        table.add_column("⏱️ Duration", style="magenta", justify="center", min_width=8)
        table.add_column("🔄 Max Trials", style="white", justify="center", min_width=10)
        table.add_column("⚡ Interval", style="white", justify="center", min_width=8)

        for task in tasks:
            from datetime import datetime

            begin_dt = datetime.fromtimestamp(task.begin_time)
            table.add_row(
                task.user_name,
                str(task.floor_id),
                str(task.seat_number),
                begin_dt.strftime("%Y-%m-%d %H:%M"),
                f"{task.duration}h",
                str(task.max_trials),
                f"{task.interval}s",
            )

        console.print(table)

    @staticmethod
    def display_results(results: List[BookingResult]) -> None:
        """显示预订结果"""
        if not results:
            console.print("[yellow]⚠️  No results to display[/yellow]")
            return

        table = Table(
            title="📊 Booking Results Summary",
            show_header=True,
            header_style="bold magenta",
            border_style="blue",
        )

        table.add_column("👤 User", style="cyan", no_wrap=True, min_width=12)
        table.add_column("📍 Seat Info", style="blue", min_width=15)
        table.add_column("✅ Status", style="green", justify="center", min_width=10)
        table.add_column("⏰ Booking Time", style="yellow", min_width=16)
        table.add_column("⏱️ Duration", style="magenta", justify="center", min_width=8)
        table.add_column("🔄 Attempts", style="white", justify="center", min_width=8)
        table.add_column("💬 Details", style="white", max_width=30)

        for result in results:
            status = "✅ Success" if result.success else "❌ Failed"
            status_style = "bold green" if result.success else "bold red"

            booking_time = result.booking_time or "N/A"
            duration = result.duration or "N/A"
            attempts = str(result.attempt or result.attempts or "N/A")

            details = (
                result.message if result.success else result.error or "Unknown error"
            )
            if len(details) > 40:
                details = details[:37] + "..."

            table.add_row(
                result.user,
                result.seat_info,
                f"[{status_style}]{status}[/{status_style}]",
                booking_time,
                duration,
                attempts,
                details,
            )

        console.print(table)


@app.command()
def book():
    """🎯 Book library seats based on configuration"""
    console.print("🏫 [bold blue]HDU Library Seat Booking System[/bold blue] 🏫\n")

    try:
        import os

        config_content = os.environ.get("config", "")

        # 解析配置
        with console.status("[bold green]🔍 Parsing configuration..."):
            tasks = ConfigParser.parse_config_string(config_content)

        if not tasks:
            console.print("[yellow]⚠️  No valid tasks found in configuration[/yellow]")
            raise typer.Exit(1)

        console.print(f"[green]✅ Found {len(tasks)} booking task(s)[/green]\n")

        # 显示任务摘要
        ResultsDisplayer.display_task_summary(tasks)

        # 执行预订
        console.print("\n[bold green]🎯 Starting booking process...[/bold green]\n")

        config_manager = ConfigManager()
        booking_service = BookingService(config_manager)

        import asyncio

        results = asyncio.run(booking_service.run_multiple_tasks(tasks))

        console.print()
        # 显示结果
        ResultsDisplayer.display_results(results)

    except Exception as e:
        console.print(f"[red]💥 Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def validate(
    config_file: Path = typer.Argument(..., help="📄 Configuration file to validate"),
):
    """✅ Validate configuration file"""
    if not config_file.exists():
        console.print(f"[red]❌ Configuration file {config_file} not found[/red]")
        raise typer.Exit(1)

    try:
        config_content = config_file.read_text(encoding="utf-8")
        tasks = ConfigParser.parse_config_string(config_content)

        console.print(
            f"[green]✅ Configuration is valid! Found {len(tasks)} task(s)[/green]"
        )

        # 显示简单摘要
        for i, task in enumerate(tasks, 1):
            from datetime import datetime

            begin_dt = datetime.fromtimestamp(task.begin_time)
            console.print(
                f"  {i}. [cyan]{task.user_name}[/cyan] - "
                f"Floor {task.floor_id}, Seat {task.seat_number} "
                f"at {begin_dt.strftime('%Y-%m-%d %H:%M')}"
            )

    except Exception as e:
        console.print(f"[red]❌ Configuration error: {e}[/red]")
        raise typer.Exit(1)
