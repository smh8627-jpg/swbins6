"""설정 저장/불러오기. 비밀번호는 Windows DPAPI(현재 Windows 사용자 전용)로 암호화해서 저장한다."""
import base64
import ctypes
import json
import os
from ctypes import wintypes
from datetime import date

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AinBooker")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "booker.log")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text: str) -> str:
    if not text:
        return ""
    src, _keep = _blob(text.encode("utf-8"))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI 암호화 실패")
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(token: str) -> str:
    if not token:
        return ""
    try:
        src, _keep = _blob(base64.b64decode(token))
        out = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return ""  # 다른 PC/계정에서 복사해 온 설정이면 복호화되지 않는다


def default_config():
    today = date.today()
    # 기본값: 다음 달 1일 ~ 그 다음 달 말일 (다음 달 오픈 시 자동으로 대상에 포함)
    y, m = (today.year + (today.month // 12), today.month % 12 + 1)
    y2, m2 = (y + (m // 12), m % 12 + 1)
    y3, m3 = (y2 + (m2 // 12), m2 % 12 + 1)
    last = date.fromordinal(date(y3, m3, 1).toordinal() - 1)
    return {
        "user_id": "",
        "password_enc": "",
        "child": "",
        "doctors": ["나지윤", "신영주", "김효진(6진료실)"],
        "extra_doctors": "",  # "이름=코드, 이름=코드"
        "date_from": date(y, m, 1).isoformat(),
        "date_to": last.isoformat(),
        "weekdays": [0, 1, 2, 3, 4, 5],  # 월=0 ... 토=5
        "times": ["1000", "1100", "1200", "1500"],
        "interval_sec": 45,
        "burst": True,  # 매월 1일 09:00 전후 집중 감시
        "auto_book": True,  # False면 알림만
        "exit_on_success": True,  # 예약 완료 후 프로그램 종료
        "visit_type": "infant",  # infant=영유아검진, general=일반 진료
        "time_from": "0900",  # 일반 진료 시간대
        "time_to": "1800",
        "date_mode": "range",  # range=기간+요일, pick=달력에서 고른 날짜만
        "picked_dates": [],  # ["2026-11-18", ...]
        "save_login": True,  # 아이디/비밀번호를 이 PC에 (암호화해서) 저장
        "max_failures": 5,
        "booked": None,  # 예약 성공 기록 {"child","doctor","date","time"} - 중복 예약 방지
    }


def load():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
