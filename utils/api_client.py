import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from utils.config import ConfigManager
from utils.console import logger


class RoomsCacheManager:
    """房间缓存管理器"""

    def __init__(self, cache_ttl_hours: int = 1):
        self._cache: dict | None = None
        self._cache_timestamp: datetime | None = None
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._lock = asyncio.Lock()

    async def get_cache(self) -> dict | None:
        """获取缓存数据"""
        async with self._lock:
            if self._is_cache_valid():
                logger.debug("Using cached rooms data")
                return self._cache
            return None

    async def set_cache(self, data: dict) -> None:
        """设置缓存数据"""
        async with self._lock:
            self._cache = data
            self._cache_timestamp = datetime.now()
            logger.debug(f"Rooms cache updated with {len(data)} rooms")

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if self._cache is None or self._cache_timestamp is None:
            return False
        return datetime.now() - self._cache_timestamp < self._cache_ttl

    async def clear_cache(self) -> None:
        """清空缓存"""
        async with self._lock:
            self._cache = None
            self._cache_timestamp = None
            logger.info("Rooms cache cleared")


class LibraryAPIClient:
    """图书馆API客户端"""

    def __init__(
        self,
        config_manager: ConfigManager,
        cache_manager: RoomsCacheManager | None = None,
    ):
        self.config = config_manager.config
        self.session: httpx.AsyncClient | None = None
        self.uid: str | None = None
        self._cache_manager = cache_manager or RoomsCacheManager()

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

    async def request(self, method: str, endpoint: str, **kwargs) -> dict:
        """发送HTTP请求"""
        url = self.endpoints.get(endpoint, endpoint)

        if not url:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        logger.debug(f"Making {method.upper()} request to: {url}")

        try:
            response = await self.session.request(method.upper(), url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Request failed: {e}")
            raise

    async def login(self, username: str, password: str) -> str | None:
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
                logger.info(f"Login response: {response}")
                return None

            self.uid = response["DATA"]["uid"]
            logger.info(f"Login successful for user: {username}")
            return self.uid

        except Exception as e:
            logger.error(f"Login error for {username}: {e}")
            return None

    async def get_rooms_dict(
        self, force_refresh: bool = False, use_json: bool = True
    ) -> dict:
        """获取房间字典（支持JSON文件和API缓存）"""
        # 优先尝试从JSON文件读取
        if use_json and not force_refresh:
            json_data = await self._load_rooms_from_json()
            if json_data:
                logger.debug("Using rooms data from JSON file")
                return json_data

        # 如果JSON文件不存在或需要强制刷新，则使用内存缓存
        if not force_refresh:
            cached_data = await self._cache_manager.get_cache()
            if cached_data is not None:
                return cached_data

        try:
            rooms_data = await self.query_rooms()
            new_data = await self.query_seats(rooms_data)
            await self._cache_manager.set_cache(new_data)
            return new_data
        except Exception as e:
            logger.error(f"Failed to fetch rooms data: {e}")
            return {}

    async def _load_rooms_from_json(self) -> dict:
        """从JSON文件加载房间数据"""
        json_file = Path("./data/rooms_cache.json")

        if not json_file.exists():
            logger.debug("JSON cache file not found")
            return {}

        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            # 检查数据格式
            if "rooms" not in data:
                logger.warning("Invalid JSON cache format")
                return {}

            # 检查数据是否过期（可选：设置过期时间）
            if "metadata" in data and "generated_at" in data["metadata"]:
                generated_time = datetime.fromisoformat(
                    data["metadata"]["generated_at"]
                )
                if datetime.now() - generated_time > timedelta(hours=24):
                    logger.info(
                        "JSON cache is older than 24 hours, consider refreshing"
                    )

            logger.info(f"Loaded rooms data from JSON: {len(data['rooms'])} rooms")
            return data["rooms"]

        except Exception as e:
            logger.error(f"Failed to load rooms from JSON: {e}")
            return {}

    async def clear_rooms_cache(self) -> None:
        """清空房间缓存"""
        await self._cache_manager.clear_cache()

    async def update_json_cache(self) -> bool:
        """更新JSON缓存文件"""
        try:
            logger.info("正在更新JSON缓存...")
            rooms_data = await self.query_rooms()
            if not rooms_data:
                logger.error("未能获取房间数据")
                return False

            seats_data = await self.query_seats(rooms_data)
            if not seats_data:
                logger.error("未能获取座位数据")
                return False

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

            logger.success(f"JSON缓存已更新: {json_file.absolute()}")
            return True

        except Exception as e:
            logger.error(f"更新JSON缓存失败: {e}")
            return False

    async def query_rooms(self) -> dict:
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

            # 检查响应格式 - 登录后的响应格式不同
            if "content" in response:
                data_key = "content"
            elif "DATA" in response:
                data_key = "DATA"
            else:
                logger.error(
                    f"Invalid response format from category_list. Response keys: {list(response.keys()) if response else 'None'}"
                )
                if response:
                    logger.info(f"Full response: {response}")
                return rooms

            # 安全地访问嵌套数据
            try:
                room_items = response[data_key]["children"][1]["defaultItems"]

            except (KeyError, IndexError) as e:
                logger.error(f"Failed to extract room items from response: {e}")
                logger.info(f"Full response: {response}")
                return rooms

            for room in room_items:
                try:
                    room_name = room["name"]
                    room_url = unquote(room["link"]["url"])
                    parsed_url = urlparse(room_url)

                    if not parsed_url.query:
                        logger.warning(f"No query parameters for room: {room_name}")
                        continue

                    query_params = parse_qs(parsed_url.query)
                    params = {k: v[0] for k, v in query_params.items() if v}
                    params.update({"LAB_JSON": "1"})

                    room_data = await self.request("get", "search_seats", params=params)

                    if room_data and room_data.get("data"):
                        rooms[room_name] = room_data["data"]

                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(
                        f"Error processing room {room.get('name', 'unknown')}: {e}"
                    )
                    continue

        except Exception as e:
            logger.error(f"Failed to query rooms: {e}")

        return rooms

    async def query_seats(self, rooms: dict) -> dict:
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

        logger.info(
            f"Looking up seat - floor_id: {floor_id}, seat_number: {seat_number}"
        )

        room_name = self.config.room_name_dict.get(floor_id)
        floor_name = self.config.floor_name_dict.get(floor_id)

        if not room_name or not floor_name:
            logger.error(f"Invalid floor_id: {floor_id} - no mapping found")
            return 0

        try:
            rooms = await self.get_rooms_dict()

            if room_name in rooms:
                room_floors = rooms[room_name]

                if floor_name in room_floors:
                    floor_data = room_floors[floor_name]
                    available_seats = floor_data.get("seats", {})

                    seat_id = available_seats.get(str(seat_number), 0)
                    if seat_id == 0:
                        logger.error(
                            f"Seat {seat_number} not found on floor {floor_id}"
                        )
                        logger.error(f"Available seats: {list(available_seats.keys())}")
                    else:
                        logger.info(
                            f"Found seat {seat_number} on floor {floor_id} with ID {seat_id}"
                        )

                    return seat_id
                else:
                    logger.error(f"Floor {floor_name} not found in room {room_name}")
                    return 0
            else:
                logger.error(f"Room {room_name} not found in available rooms")
                return 0

        except Exception as e:
            logger.error(f"Error getting seat ID for {floor_id}/{seat_number}: {e}")
            return 0

    async def get_seat_info(self, seat_id: str, space_id: str) -> dict:
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

        # 调试：显示传入的参数
        begin_dt = datetime.fromtimestamp(begin_time)
        logger.info(
            f"Booking parameters - seat_id: {seat_id}, begin_time: {begin_time} ({begin_dt}), duration: {duration}h, uid: {self.uid}"
        )

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

        logger.info(f"API request data: {confirm_data}")

        try:
            # 生成API Token
            api_token = self._generate_api_token(confirm_data)
            self.session.headers["Api-Token"] = api_token

            response = await self.request(
                "post",
                "reserve_seat",
                params={
                    "LAB_JSON": "1",
                },
                data=confirm_data,
            )
            return self._handle_booking_response(
                response, seat_id, begin_time, duration
            )

        except Exception as e:
            logger.error(f"Error during seat confirmation: {e}")
            return "request_error"

    def _generate_api_token(self, data: dict) -> str:
        """生成API Token"""
        data_string = "post&/Seat/Index/bookSeats?LAB_JSON=1&" + "&".join(
            f"{k}={v}" for k, v in data.items()
        )
        md5_hash = hashlib.md5(data_string.encode("utf-8")).hexdigest()
        return base64.b64encode(md5_hash.encode("utf-8")).decode("utf-8")

    def _handle_booking_response(
        self, response: dict, seat_id: int, begin_time: int, duration: int
    ) -> str:
        """处理预订响应"""
        booking_time_str = datetime.fromtimestamp(begin_time).strftime("%m月%d日%H点")
        message = (
            f"seat_id: {seat_id}, begin_time: {booking_time_str}, duration: {duration}h"
        )

        if response.get("CODE") == "ok":
            logger.success(f"Seat booking successful: {message}")
            return "ok"

        error_code = response.get("CODE", "UNKNOWN")
        error_msg = self.config.state_dict.get(
            error_code, f"Unknown error: {error_code}"
        )
        logger.error(f"Seat booking failed: {message}, Error: {error_msg}")
        return error_msg
