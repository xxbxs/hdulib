import asyncio
import datetime as dt
import os
import re
import time
from typing import List

from utils.hdulib import (
    HDULIB,
    Task,
    User,
    get_seat_by_room_and_floor,
)


async def run(user: HDULIB):
    rooms = await user.get_rooms_dict()
    for idx, task in enumerate(user.tasks):
        seat_id = get_seat_by_room_and_floor(rooms, task.floor_id, task.seat_number)
        if seat_id == 0:
            print(
                f"task_id: {idx} floor_id: {task.floor_id}, seat_number: {task.seat_number} is not found"
            )
        else:
            print(
                f"task_id: {idx} floor_id: {task.floor_id}, seat_number: {task.seat_number} seat_id: {seat_id}"
            )
        if task.begin_time < dt.datetime.now().timestamp():
            print(f"task_id: {idx} begin_time 已调整到下一天")
            task.begin_time += int(dt.timedelta(days=1).total_seconds())

        if dt.datetime.fromtimestamp(task.begin_time).day - 1 > dt.datetime.now().day:
            if dt.datetime.now().hour < 20:
                print("等待到20点开始执行")
                await asyncio.sleep(
                    (
                        dt.datetime.now().replace(hour=20, minute=0, second=0)
                        - dt.datetime.now()
                    ).total_seconds()
                )

        for _ in range(task.max_trials):
            message = await user.confirm_seat(
                task.begin_time, task.duration, user.uid, seat_id
            )
            if message == "ok":
                print(f"task_id: {idx} Seat reservation successful.")
                break

            await asyncio.sleep(task.interval)


def parse_config(config: str) -> User:
    config_dict = dict(re.findall(r"(\w+)\s*=\s*(\S+)", config))

    user_name = config_dict.get("user_name")
    password = config_dict.get("password")
    floor_id = str(config_dict.get("floor_id"))
    seat_number = str(config_dict.get("seat_number"))
    begin_time = int(config_dict.get("begin_time"))
    duration = int(config_dict.get("duration"))
    max_trials = int(config_dict.get("max_trials"))
    interval = int(config_dict.get("interval"))

    task = Task(
        floor_id=floor_id,
        seat_number=seat_number,
        begin_time=(dt.datetime.now() + dt.timedelta(days=2))
        .replace(hour=begin_time, minute=0, second=0, microsecond=0)
        .timestamp(),
        duration=duration,
        max_trials=max_trials,
        interval=interval,
    )

    return User(user_name=user_name, password=password, tasks=[task])


async def main():
    config = os.environ.get("CONFIG", "")
    print(config)
    user = HDULIB(parse_config(config))
    await run(user)


if __name__ == "__main__":
    asyncio.run(main())
