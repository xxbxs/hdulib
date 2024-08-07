import asyncio
import base64
import datetime as dt
import hashlib
import time
from time import sleep
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote

import httpx
import toml
from pydantic import BaseModel


class Task(BaseModel):
    user_name: str = ""
    password: str = ""
    floor_id: Optional[Union[str, int]] = None
    seat_number: Optional[Union[str, int]] = None
    begin_time: Optional[Union[str, int]] = None
    duration: Optional[Union[str, int]] = None
    max_trials: Optional[int] = 3
    interval: Optional[int] = 2


def create_session() -> httpx.Client:
    session = httpx.Client()
    session.headers = config_data.get("init_headers")
    session.params = {"LAB_JSON": "1"}
    return session


def seat_msg_paser(msg: Dict) -> None:
    seat_times_data = msg["data"]["seat_times"]
    format_str = "{:>3}  _confirm:{:%H:%M:%S} _check:{:%H:%M:%S} _bg: {:%H:%M:%S} _ed: {:%H:%M:%S}  _user:{:<10}"

    for idx, msg in enumerate(seat_times_data):
        user = msg["booking"].get("user", {"format": "Nobody"})["format"]
        print(
            format_str.format(
                idx,
                dt.datetime.fromtimestamp(float(msg["booking"].get("confirm_time", 0))),
                dt.datetime.fromtimestamp(int(msg["booking"].get("check_in_time", 0))),
                dt.datetime.fromtimestamp(int(msg["begin_time"])),
                dt.datetime.fromtimestamp(int(msg["end_time"])),
                user,
            )
        )


def get_seat_by_room_and_floor(
    rooms: Dict[str, Dict[str, Dict[str, Dict[str, int]]]],
    floor_id: str,
    seat_number: int,
) -> int:
    room_name = get_room_name_by_floor_id(floor_id)
    floor_name = get_floor_name_by_floor_id(floor_id)

    if room_name is None or floor_name is None:
        return 0

    return (
        rooms.get(room_name, {})
        .get(floor_name, {})
        .get("seats", {})
        .get(str(seat_number), 0)
    )


def get_room_name_by_floor_id(floor_id):
    return config_data.get("room_name_dict").get(floor_id)


def get_floor_name_by_floor_id(floor_id):
    return config_data.get("floor_name_dict").get(floor_id)


def load_config() -> Dict:
    return toml.load("./utils/config.toml")


config_data = load_config()


class HDULIB:
    rooms = None

    def __init__(self, task: Task) -> None:
        self.session = create_session()
        self.task = task
        self.url_list = config_data.get("url")
        self.uid = 0
        self.login()
        if HDULIB.rooms is None:
            print("fetch rooms data ...")
            HDULIB.rooms = self.get_rooms_dict()
            print("fetch rooms data done.")

    def fetch(
        self,
        method: str,
        url: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        _tojson: bool = False,
    ) -> Any:
        if method.lower() == "get":
            res = self.session.get(url, params=params)
        elif method.lower() == "post":
            res = self.session.post(url, data=data)

        return res.json() if _tojson else res

    def login(self) -> str:
        login_data = {
            "login_name": self.task.user_name,
            "org_id": "104",
            "password": self.task.password,
        }

        res = self.fetch(
            "post",
            self.url_list["LOGIN_URL"],
            data=login_data,
            _tojson=True,
        )

        print(f"login response: {login_data}")

        self.uid = res["DATA"]["uid"]

        return res

    def get_seat_data(
        self,
        seat_number: Optional[Union[str, int]],
        space_id: Optional[Union[str, int]],
    ) -> dict:
        seat_param = {
            "seat_id": get_seat_by_room_and_floor(space_id, seat_number),
            "space_id": space_id,
            "library_id": "104",
            "LAB_JSON": "1",
        }

        res = self.fetch(
            "get",
            self.url_list["SEAT_CHECK_STATE_URL"],
            params=seat_param,
            _tojson=True,
        )

        print(
            f"get_seat_data start, seat_id : {seat_param['seat_id']} {res['header']['title']}"
        )

        return res

    def __queryRooms(self):
        queryRoomsRes = self.fetch(
            "get", self.url_list["CATEGORY_LIST_URL"], _tojson=True
        )

        rawRooms = queryRoomsRes["content"]["children"][1]["defaultItems"]

        rooms = {x["name"]: unquote(x["link"]["url"]).split("?")[1] for x in rawRooms}

        for room in rooms.keys():
            _room_res = self.fetch(
                "get",
                f"{self.url_list['SEARCH_SEATS_URL']}?{rooms[room]}",
                _tojson=True,
            )
            _room = _room_res["data"]

            if _room is None:
                return None

            rooms[room] = _room

            sleep(2)

        return rooms

    def __querySeats(self, rooms: dict):
        _time = dt.datetime.now()

        ret_room_dict = dict()

        if _time.hour >= 22:
            _time = _time + dt.timedelta(days=1)
            _time = _time.replace(hour=11, minute=0, second=0)

        if _time.hour < 8:
            _time = _time.replace(hour=11, minute=0, second=0)

        for room_name, room_data in rooms.items():
            data = {
                "beginTime": int(_time.timestamp()),
                "duration": 3600,
                "num": 1,
                "space_category[category_id]": room_data["space_category"][
                    "category_id"
                ],
                "space_category[content_id]": room_data["space_category"]["content_id"],
            }

            resp = self.fetch(
                "post", self.url_list["SEARCH_SEATS_URL"], data=data, _tojson=True
            )

            room_data["floors"] = {
                x["roomName"]: x
                for x in resp["allContent"]["children"][2]["children"]["children"]
            }

            for floor, floor_data in room_data["floors"].items():
                seat_map = floor_data["seatMap"]
                seats = {poi["title"]: poi["id"] for poi in seat_map["POIs"]}
                seat_id = seat_map["info"]["id"]

                room_data["floors"][floor] = {"seats": seats, "seat_id": seat_id}

            ret_room_dict[room_name] = room_data["floors"]

            time.sleep(1)

        return ret_room_dict

    def get_rooms_dict(self):
        return self.__querySeats(self.__queryRooms())

    def confirm_seat(
        self,
        beginTime: int,
        duration: int,
        user_id: Optional[str | int],
        seat_id: Optional[str | int],
    ) -> str:
        confirm_data = {
            "api_time": str(
                int(dt.datetime.now().replace(second=0, minute=0).timestamp())
            ),
            "beginTime": str(beginTime),
            "duration": str(3600 * duration),
            "is_recommend": "1",
            "seatBookers[0]": user_id,
            "seats[0]": seat_id,
        }

        data_string = "post&/Seat/Index/bookSeats?LAB_JSON=1&" + "&".join(
            f"{k}={v}" for k, v in confirm_data.items()
        )
        md5 = hashlib.md5(data_string.encode("utf-8")).hexdigest()

        str_g = base64.b64encode(md5.encode("utf-8")).decode("utf-8")

        self.session.headers["Api-Token"] = str_g

        res = self.fetch(
            "post",
            self.url_list["RESERVE_SEAT_URL"],
            data=confirm_data,
            _tojson=True,
        )
        print(res)
        message = f"seat_id : {seat_id}  begin_time:{dt.datetime.fromtimestamp(beginTime).strftime('%m月%d日%H点')}  duration:{duration} h"

        if res["CODE"] == "ok":
            print(f"confirm_seat success, {message}")
            return "ok"
        else:
            print(f"confirm_seat fail, {message}")
            return config_data.get("state_dict").get(res["CODE"])
