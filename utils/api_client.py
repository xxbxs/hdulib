import asyncio
import base64
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from loguru import logger

from utils.config import ConfigManager


class RoomsCacheManager:
    """全局房间缓存管理器 - 单例模式"""

    _instance = None
    _lock = threading.Lock()
    _async_lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._cache: Optional[Dict] = None
            self._cache_timestamp: Optional[datetime] = None
            self._cache_ttl = timedelta(hours=1)  # 缓存有效期1小时
            self._initialized = True

    async def get_cache(self) -> Optional[Dict]:
        """获取缓存数据"""
        async with self._async_lock:
            if self._is_cache_valid():
                logger.debug("Using cached rooms data")
                return self._cache
            return None

    async def set_cache(self, data: Dict) -> None:
        """设置缓存数据"""
        async with self._async_lock:
            self._cache = data
            self._cache_timestamp = datetime.now()
            logger.info(f"Rooms cache updated with {len(data)} rooms")

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if self._cache is None or self._cache_timestamp is None:
            return False

        return datetime.now() - self._cache_timestamp < self._cache_ttl

    async def clear_cache(self) -> None:
        """清空缓存"""
        async with self._async_lock:
            self._cache = None
            self._cache_timestamp = None
            logger.info("Rooms cache cleared")

    async def refresh_cache(self, fetch_func) -> Dict:
        """刷新缓存"""
        async with self._async_lock:
            logger.info("Refreshing rooms cache...")
            try:
                new_data = await fetch_func()
                self._cache = new_data
                self._cache_timestamp = datetime.now()
                logger.info(f"Rooms cache refreshed with {len(new_data)} rooms")
                return new_data
            except Exception as e:
                logger.error(f"Failed to refresh cache: {e}")
                # 如果刷新失败但有旧缓存，返回旧缓存
                if self._cache is not None:
                    logger.warning("Using stale cache data due to refresh failure")
                    return self._cache
                raise


class LibraryAPIClient:
    """图书馆API客户端 - 完全异步版本，使用全局缓存"""

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager.config
        self.session: Optional[httpx.AsyncClient] = None
        self.uid: Optional[str] = None

        # 使用全局单例缓存管理器
        self._cache_manager = RoomsCacheManager()

        # 端点映射
        self.endpoints = {
            "category_list": self.config.category_list_url,
            "search_seats": self.config.search_seats_url,
            "seat_state": self.config.seat_state_url,
            "reserve_seat": self.config.reserve_seat_url,
            "login": self.config.login_url,
        }

    async def __aenter__(self):
        self.session = httpx.AsyncClient(
            timeout=30.0,
            headers=self.config.init_headers,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()

    async def request(self, method: str, endpoint: str, **kwargs):
        if endpoint in self.endpoints:
            url = self.endpoints[endpoint]
        else:
            url = endpoint

        if not url:
            logger.error(f"Empty URL for endpoint: {endpoint}")
            raise ValueError(f"Invalid endpoint: {endpoint}")

        logger.debug(f"Making {method.upper()} request to: {url}")
        if kwargs.get("data"):
            logger.debug(f"Request data: {kwargs['data']}")
        if kwargs.get("params"):
            logger.debug(f"Request params: {kwargs['params']}")

        try:
            response = await self.session.request(method.upper(), url, **kwargs)
            response.raise_for_status()
            result = response.json()
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e}")
            raise
        except httpx.HTTPError as e:
            logger.error(f"Request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during request: {e}")
            raise

    async def login(self, username: str, password: str) -> Optional[str]:
        """用户登录"""
        if not username or not password:
            logger.error("Username and password are required")
            return None

        login_data = {
            "login_name": username,
            "org_id": self.config.org_id,
            "password": password,
        }

        try:
            response = await self.request(
                "post",
                "login",
                params={
                    "LAB_JSON": "1",
                },
                data=login_data,
            )
            if response.get("CODE") != "ok":
                error_msg = response.get("MSG", "Unknown error")
                logger.error(f"Login failed for {username}: {error_msg}")
                return None

            self.uid = response["DATA"]["uid"]
            logger.info(f"Login successful for user: {username}")
            return self.uid

        except Exception as e:
            logger.error(f"Login error for {username}: {e}")
            return None

    async def get_rooms_dict(self, force_refresh: bool = False) -> Dict:
        """获取房间字典（使用全局缓存）"""
        # 如果不强制刷新，先尝试从缓存获取
        if not force_refresh:
            cached_data = await self._cache_manager.get_cache()
            if cached_data is not None:
                return cached_data

        # 缓存无效或强制刷新，重新获取数据
        async def fetch_rooms_data():
            logger.info("Fetching fresh rooms data...")
            rooms_data = await self.query_rooms()
            return await self.query_seats(rooms_data)

        if force_refresh:
            return await self._cache_manager.refresh_cache(fetch_rooms_data)
        else:
            try:
                new_data = await fetch_rooms_data()
                await self._cache_manager.set_cache(new_data)
                return new_data
            except Exception as e:
                logger.error(f"Failed to fetch rooms data: {e}")
                return {}

    async def clear_rooms_cache(self):
        """清空房间缓存"""
        await self._cache_manager.clear_cache()

    async def query_rooms(self) -> Dict:
        """获取所有可用房间"""
        rooms = {}

        try:
            response = await self.request(
                "get",
                "category_list",
                params={
                    "LAB_JSON": "1",
                },
            )

            if not response or "content" not in response:
                logger.error("Invalid response format from category_list")
                return rooms

            # 安全地访问嵌套数据
            try:
                room_items = response["content"]["children"][1]["defaultItems"]
            except (KeyError, IndexError) as e:
                logger.error(f"Failed to extract room items from response: {e}")
                return rooms

            for room in room_items:
                try:
                    room_name = room["name"]
                    room_url = unquote(room["link"]["url"])
                    parsed_url = urlparse(room_url)

                    if not parsed_url.query:
                        logger.warning(f"No query parameters for room: {room_name}")
                        continue

                    # 解析查询参数为字典
                    query_params = parse_qs(parsed_url.query)
                    params = {k: v[0] for k, v in query_params.items() if v}
                    params.update(
                        {
                            "LAB_JSON": "1",
                        }
                    )
                    logger.debug(f"Querying room {room_name} with params: {params}")

                    room_data = await self.request("get", "search_seats", params=params)

                    if room_data and room_data.get("data"):
                        rooms[room_name] = room_data["data"]
                        logger.info(f"Found room: {room_name}")

                    await asyncio.sleep(0.5)  # Rate limiting

                except Exception as e:
                    logger.error(
                        f"Error processing room {room.get('name', 'unknown')}: {e}"
                    )
                    continue

        except Exception as e:
            logger.error(f"Failed to query rooms: {e}")

        return rooms

    async def query_seats(self, rooms: Dict) -> Dict:
        """获取所有房间的可用座位"""
        if not rooms:
            logger.warning("No rooms data provided")
            return {}

        # 计算预订时间
        now = datetime.now()
        if now.hour >= 22:
            booking_time = (now + timedelta(days=1)).replace(
                hour=11, minute=0, second=0, microsecond=0
            )
        elif now.hour < 8:
            booking_time = now.replace(hour=11, minute=0, second=0, microsecond=0)
        else:
            booking_time = now

        result = {}
        for room_name, room_data in rooms.items():
            try:
                # 验证房间数据结构
                if not isinstance(room_data, dict) or "space_category" not in room_data:
                    logger.warning(f"Invalid room data for {room_name}")
                    continue

                space_category = room_data["space_category"]
                if not isinstance(space_category, dict):
                    logger.warning(f"Invalid space_category for {room_name}")
                    continue

                data = {
                    "beginTime": int(booking_time.timestamp()),
                    "duration": 3600,
                    "num": 1,
                    "space_category[category_id]": space_category.get("category_id"),
                    "space_category[content_id]": space_category.get("content_id"),
                }

                response = await self.request(
                    "post",
                    "search_seats",
                    params={
                        "LAB_JSON": "1",
                    },
                    data=data,
                )

                floors = {}
                try:
                    floor_children = response["allContent"]["children"][2]["children"][
                        "children"
                    ]
                    for floor in floor_children:
                        if not isinstance(floor, dict):
                            continue

                        floor_name = floor.get("roomName", "Unknown")
                        seat_map = floor.get("seatMap", {})

                        if not isinstance(seat_map, dict):
                            continue

                        pois = seat_map.get("POIs", [])
                        seat_map_info = seat_map.get("info", {})

                        floors[floor_name] = {
                            "seats": {
                                poi["title"]: poi["id"]
                                for poi in pois
                                if isinstance(poi, dict)
                                and "title" in poi
                                and "id" in poi
                            },
                            "seat_id": seat_map_info.get("id")
                            if isinstance(seat_map_info, dict)
                            else None,
                        }

                except (KeyError, IndexError, TypeError) as e:
                    logger.error(f"Error parsing floors for {room_name}: {e}")
                    continue

                if floors:
                    result[room_name] = floors
                    logger.info(f"Processed {len(floors)} floors for room {room_name}")

            except Exception as e:
                logger.error(f"Error processing room {room_name}: {e}")
                continue
        return result

    async def get_seat_id(self, floor_id: str, seat_number: str) -> int:
        """获取座位ID"""
        if not floor_id or not seat_number:
            logger.error("floor_id and seat_number are required")
            return 0

        room_name = self.config.room_name_dict.get(floor_id)
        floor_name = self.config.floor_name_dict.get(floor_id)
        if not room_name or not floor_name:
            logger.error(f"Invalid floor_id: {floor_id}")
            return 0

        try:
            rooms = await self.get_rooms_dict()
            seat_id = (
                rooms.get(room_name, {})
                .get(floor_name, {})
                .get("seats", {})
                .get(str(seat_number), 0)
            )
            if seat_id == 0:
                logger.error(f"Seat {seat_number} not found on floor {floor_id}")
            else:
                logger.info(
                    f"Found seat {seat_number} on floor {floor_id} with ID {seat_id}"
                )

            return seat_id

        except Exception as e:
            logger.error(f"Error getting seat ID for {floor_id}/{seat_number}: {e}")
            return 0

    async def get_seat_info(self, seat_id: str, space_id: str) -> Dict:
        """获取特定座位信息"""
        if not seat_id or not space_id:
            logger.error("seat_id and space_id are required")
            return {}

        params = {
            "seat_id": seat_id,
            "space_id": space_id,
            "library_id": self.config.library_id,
        }

        try:
            return await self.request("get", "seat_state", params=params)
        except Exception as e:
            logger.error(f"Error getting seat info for seat {seat_id}: {e}")
            return {}

    async def confirm_seat(self, begin_time: int, duration: int, seat_id: int) -> str:
        """确认座位预订"""
        if not self.uid:
            logger.error("User not logged in")
            return "not_logged_in"

        if begin_time <= 0 or duration <= 0 or seat_id <= 0:
            logger.error("Invalid parameters for seat confirmation")
            return "invalid_parameters"

        confirm_data = {
            "api_time": str(
                int(datetime.now().replace(second=0, minute=0).timestamp())
            ),
            "beginTime": str(begin_time),
            "duration": str(3600 * duration),
            "is_recommend": "1",
            "seatBookers[0]": self.uid,
            "seats[0]": str(seat_id),
        }

        try:
            # 生成API Token
            api_token = self._generate_api_token(confirm_data)
            self.session.headers["Api-Token"] = api_token

            response = await self.request("post", "reserve_seat", data=confirm_data)
            return self._handle_booking_response(
                response, seat_id, begin_time, duration
            )

        except Exception as e:
            logger.error(f"Error during seat confirmation: {e}")
            return "request_error"

    def _generate_api_token(self, data: Dict) -> str:
        """生成API Token"""
        try:
            data_string = "post&/Seat/Index/bookSeats?LAB_JSON=1&" + "&".join(
                f"{k}={v}" for k, v in data.items()
            )
            md5_hash = hashlib.md5(data_string.encode("utf-8")).hexdigest()
            return base64.b64encode(md5_hash.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.error(f"Error generating API token: {e}")
            return ""

    def _handle_booking_response(
        self, response: Dict, seat_id: int, begin_time: int, duration: int
    ) -> str:
        """处理预订响应"""
        try:
            booking_time_str = datetime.fromtimestamp(begin_time).strftime(
                "%m月%d日%H点"
            )
            message = f"seat_id: {seat_id}, begin_time: {booking_time_str}, duration: {duration}h"

            if response.get("CODE") == "ok":
                logger.success(f"Seat booking successful: {message}")
                return "ok"
            else:
                error_code = response.get("CODE", "UNKNOWN")
                error_msg = self.config.state_dict.get(
                    error_code, f"Unknown error: {error_code}"
                )
                logger.error(f"Seat booking failed: {message}, Error: {error_msg}")
                return error_msg

        except Exception as e:
            logger.error(f"Error handling booking response: {e}")
            return "response_error"


# 为了向后兼容，提供一个同步包装器
class SyncLibraryAPIClient:
    """同步包装器，用于向后兼容"""

    def __init__(self, config_manager: ConfigManager):
        self.async_client = LibraryAPIClient(config_manager)
        self._loop = None

    def __enter__(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self.async_client.__aenter__())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._loop:
            self._loop.run_until_complete(
                self.async_client.__aexit__(exc_type, exc_val, exc_tb)
            )
            self._loop.close()

    def login(self, username: str, password: str) -> Optional[str]:
        if not self._loop:
            raise RuntimeError("Context manager not properly initialized")
        return self._loop.run_until_complete(
            self.async_client.login(username, password)
        )

    def get_seat_id(self, floor_id: str, seat_number: str) -> int:
        if not self._loop:
            raise RuntimeError("Context manager not properly initialized")
        return self._loop.run_until_complete(
            self.async_client.get_seat_id(floor_id, seat_number)
        )

    def confirm_seat(self, begin_time: int, duration: int, seat_id: int) -> str:
        if not self._loop:
            raise RuntimeError("Context manager not properly initialized")
        return self._loop.run_until_complete(
            self.async_client.confirm_seat(begin_time, duration, seat_id)
        )

    def clear_rooms_cache(self):
        """清空房间缓存"""
        if not self._loop:
            raise RuntimeError("Context manager not properly initialized")
        return self._loop.run_until_complete(self.async_client.clear_rooms_cache())

    def get_rooms_dict(self, force_refresh: bool = False) -> Dict:
        """获取房间字典（同步版本）"""
        if not self._loop:
            raise RuntimeError("Context manager not properly initialized")
        return self._loop.run_until_complete(
            self.async_client.get_rooms_dict(force_refresh)
        )
