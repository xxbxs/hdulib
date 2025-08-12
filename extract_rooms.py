#!/usr/bin/env python3
"""
房间数据提取脚本
用于提取房间和座位信息并保存到本地JSON文件
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from utils.api_client import LibraryAPIClient
from utils.config import ConfigManager
from utils.console import logger


async def extract_rooms_data():
    """提取房间数据并保存到JSON文件"""
    config_manager = ConfigManager()

    async with LibraryAPIClient(config_manager) as client:
        logger.info("开始提取房间数据...")

        # 需要登录才能访问API
        username = os.environ.get("user_name", "")
        password = os.environ.get("password", "")

        # 检查是否有登录配置文件
        login_file = Path("a.txt")
        if login_file.exists():
            login_content = login_file.read_text(encoding="utf-8").strip()
            # 解析配置格式的文件 (key = value)
            import re
            config_dict = dict(re.findall(r"(\w+)\s*=\s*(\S+)", login_content))
            if "user_name" in config_dict:
                username = config_dict["user_name"]
            if "password" in config_dict:
                password = config_dict["password"]

        if not username or not password:
            logger.error("请设置环境变量 user_name 和 password，或在 a.txt 文件中提供登录信息")
            logger.error("a.txt 格式：user_name = 用户名\\npassword = 密码")
            return False

        logger.info(f"正在登录用户: {username}")
        uid = await client.login(username, password)
        if not uid:
            logger.error("登录失败")
            return False

        # 获取房间数据
        rooms_data = await client.query_rooms()
        if not rooms_data:
            logger.error("未能获取房间数据")
            return False

        logger.info(f"成功获取 {len(rooms_data)} 个房间的基础数据")

        # 获取座位数据
        seats_data = await client.query_seats(rooms_data)
        if not seats_data:
            logger.error("未能获取座位数据")
            return False

        logger.info(f"成功获取 {len(seats_data)} 个房间的座位数据")

        # 构造完整的数据结构
        complete_data = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_rooms": len(seats_data),
                "description": "房间和座位数据缓存",
            },
            "rooms": seats_data,
        }

        # 保存到JSON文件
        json_file = Path("./data/rooms_cache.json")
        json_file.parent.mkdir(exist_ok=True)

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(complete_data, f, ensure_ascii=False, indent=2)

        logger.success(f"房间数据已保存到: {json_file.absolute()}")

        # 输出统计信息
        total_floors = sum(len(floors) for floors in seats_data.values())
        total_seats = sum(
            len(floor_data["seats"])
            for floors in seats_data.values()
            for floor_data in floors.values()
        )

        logger.info(
            f"数据统计: {len(seats_data)} 个房间, {total_floors} 个楼层, {total_seats} 个座位"
        )

        return True


async def main():
    """主函数"""
    try:
        success = await extract_rooms_data()
        if success:
            logger.success("房间数据提取完成!")
        else:
            logger.error("房间数据提取失败!")
            return 1
    except Exception as e:
        logger.error(f"提取过程中发生错误: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
