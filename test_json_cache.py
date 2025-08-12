#!/usr/bin/env python3
"""
测试JSON缓存功能的脚本
"""

import asyncio
import time
from pathlib import Path

from utils.api_client import LibraryAPIClient
from utils.config import ConfigManager
from utils.console import logger


async def test_json_cache_performance():
    """测试JSON缓存的性能"""
    config_manager = ConfigManager()

    async with LibraryAPIClient(config_manager) as client:
        # 测试1: 从API获取数据（传统方式）
        logger.info("=== 测试1: 从API获取房间数据 ===")
        start_time = time.time()
        api_data = await client.get_rooms_dict(force_refresh=True, use_json=False)
        api_time = time.time() - start_time
        logger.info(f"API方式获取数据耗时: {api_time:.2f}秒, 房间数: {len(api_data)}")

        # 更新JSON缓存
        logger.info("=== 更新JSON缓存 ===")
        success = await client.update_json_cache()
        if not success:
            logger.error("JSON缓存更新失败")
            return False

        # 测试2: 从JSON文件读取数据
        logger.info("=== 测试2: 从JSON文件读取房间数据 ===")
        start_time = time.time()
        json_data = await client.get_rooms_dict(use_json=True)
        json_time = time.time() - start_time
        logger.info(
            f"JSON方式获取数据耗时: {json_time:.2f}秒, 房间数: {len(json_data)}"
        )

        # 性能对比
        if json_time > 0:
            speedup = api_time / json_time
            logger.success(f"JSON缓存相比API加速: {speedup:.2f}倍")

        # 数据一致性检查
        logger.info("=== 数据一致性检查 ===")
        if api_data == json_data:
            logger.success("✓ API数据与JSON数据完全一致")
        else:
            logger.warning("⚠ API数据与JSON数据不一致")
            # 检查差异
            api_rooms = set(api_data.keys())
            json_rooms = set(json_data.keys())
            if api_rooms != json_rooms:
                logger.warning(
                    f"房间差异: API={len(api_rooms)}, JSON={len(json_rooms)}"
                )

        # 检查JSON文件信息
        json_file = Path("./data/rooms_cache.json")
        if json_file.exists():
            file_size = json_file.stat().st_size / 1024  # KB
            logger.info(f"JSON缓存文件大小: {file_size:.2f}KB")

        return True


async def test_seat_lookup():
    """测试座位查找功能"""
    config_manager = ConfigManager()

    async with LibraryAPIClient(config_manager) as client:
        logger.info("=== 测试座位查找功能 ===")

        # 使用JSON缓存进行座位查找
        rooms_data = await client.get_rooms_dict(use_json=True)

        if not rooms_data:
            logger.error("未能获取房间数据")
            return False

        # 显示可用房间
        logger.info("可用房间:")
        for room_name, floors in rooms_data.items():
            total_seats = sum(
                len(floor_data["seats"]) for floor_data in floors.values()
            )
            logger.info(f"  - {room_name}: {len(floors)} 个楼层, {total_seats} 个座位")

        # 测试座位ID查找（如果配置了映射）
        if (
            hasattr(config_manager.config, "room_name_dict")
            and config_manager.config.room_name_dict
        ):
            test_floor_id = list(config_manager.config.room_name_dict.keys())[0]
            test_seat = "1"  # 测试座位号

            logger.info(f"测试查找座位: floor_id={test_floor_id}, seat={test_seat}")
            start_time = time.time()
            seat_id = await client.get_seat_id(test_floor_id, test_seat)
            lookup_time = time.time() - start_time

            if seat_id > 0:
                logger.success(
                    f"✓ 找到座位ID: {seat_id}, 查找耗时: {lookup_time:.3f}秒"
                )
            else:
                logger.info(f"座位 {test_seat} 在楼层 {test_floor_id} 不可用")

        return True


async def main():
    """主测试函数"""
    logger.info("开始测试JSON缓存功能...")

    try:
        # 性能测试
        success = await test_json_cache_performance()
        if not success:
            return 1

        # 功能测试
        success = await test_seat_lookup()
        if not success:
            return 1

        logger.success("所有测试完成!")
        return 0

    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
