"""아인병원 온라인 예약 HTTP 클라이언트 (브라우저 없이 requests만 사용)."""
import json
import re
from datetime import datetime

import requests

try:  # Windows 인증서 저장소 사용 (회사망 SSL 검사 환경 대응, 검증은 유지)
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

BASE = "https://www.ainwh.co.kr"
DEPT_PD = "PD"  # 소아청소년과
DEFAULT_INFANT_TIMES = ["1000", "1100", "1200", "1500"]

# 소아청소년과 의료진 (이름 -> (의료진 코드, 영유아검진 가능 여부))
DOCTORS = {
    "나지윤": ("1100147", True),
    "신영주": ("1100148", True),
    "김효진(6진료실)": ("1100153", True),
    "최종영": ("1100145", True),
    "김효진(7진료실)": ("1100215", False),
    "김달현": ("1100019", False),
    "이경화": ("1100151", False),
    "임경인": ("1100155", False),
    "서성운": ("1100275", False),
}
# 기본 선택 의료진
DEFAULT_DOCTORS = {n: DOCTORS[n][0] for n in ("나지윤", "신영주", "김효진(6진료실)")}


# 사이트 로그인 폼은 비밀번호를 CryptoJS.AES.encrypt(pw, passphrase)로 암호화해서 보낸다.
# passphrase는 사이트가 공개 배포하는 aes.js에 고정 문자열로 들어 있다.
LOGIN_PASSPHRASE = "a19in!*"


def cryptojs_encrypt(plain, passphrase=LOGIN_PASSPHRASE, salt=None):
    """CryptoJS.AES.encrypt(string, passphrase).toString() 과 호환되는 OpenSSL 형식 base64"""
    import base64
    import hashlib
    import os
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    salt = salt or os.urandom(8)
    key_iv = b""
    prev = b""
    while len(key_iv) < 48:  # EVP_BytesToKey (MD5, 1회)
        prev = hashlib.md5(prev + passphrase.encode("utf-8") + salt).digest()
        key_iv += prev
    key, iv = key_iv[:32], key_iv[32:48]
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    return base64.b64encode(b"Salted__" + salt + ct).decode("ascii")


class SessionExpired(Exception):
    pass


class AinError(Exception):
    pass


class AinClient:
    def __init__(self, timeout=15):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        })
        self.timeout = timeout
        self.infant_times = list(DEFAULT_INFANT_TIMES)
        self.child_name = ""

    # ------------------------------------------------------------------ 기본
    def _get(self, path, **kw):
        r = self.s.get(BASE + path, timeout=self.timeout, **kw)
        r.encoding = "utf-8"
        return r

    def _post(self, path, data):
        r = self.s.post(BASE + path, data=data, timeout=self.timeout)
        r.encoding = "utf-8"
        return r

    @staticmethod
    def _need_login(text):
        return "로그인이 필요한 서비스" in text or 'name="userId"' in text

    # ------------------------------------------------------------------ 로그인
    def login(self, user_id, password):
        self._get("/member/login.do")
        r = self._post("/member/loginProc.do", {"rUrl": "", "userId": user_id, "password": cryptojs_encrypt(password)})
        probe = self._get("/reserve/verifyFamily.do")
        if self._need_login(probe.text):
            raise AinError("로그인에 실패했습니다. 아이디/비밀번호를 확인하세요.")
        return r

    def list_children(self):
        """로그인된 계정의 자녀 목록 [{name, patNo, addr1, addr2, zip, mobile}]"""
        html = self._get("/reserve/verifyFamily.do").text
        if self._need_login(html):
            raise SessionExpired()
        out = []
        for m in re.finditer(r'<option value="([^"]*\^!\^[^"]*)">([^<]+)</option>', html):
            p = m.group(1).split("^!^")
            if len(p) < 6:
                continue
            out.append({
                "name": p[0], "patNo": p[1], "addr1": p[2], "addr2": p[3],
                "zip": p[4], "mobile": p[5],
            })
        return out

    def select_child(self, name):
        """자녀를 세션에 등록(verifyProc.do) 하고 예약 화면(reservePop)을 열어 둔다."""
        kids = self.list_children()
        kid = next((k for k in kids if k["name"] == name), None)
        if not kid:
            raise AinError(f"자녀 '{name}'를 찾을 수 없습니다. (등록된 자녀: {', '.join(k['name'] for k in kids)})")
        m = re.match(r"(\d{3})-?(\d{3,4})-?(\d{4})", kid["mobile"] or "")
        if not m:
            raise AinError("자녀 연락처 정보를 읽을 수 없습니다.")
        r = self._post("/reserve/verifyProc.do", {
            "userNm": kid["name"], "patNo": kid["patNo"], "familyYn": "Y", "dutyYn": "N",
            "memberYn": "Y", "zipCode": kid["zip"], "addr1": kid["addr1"], "addr2": kid["addr2"],
            "mobile1": m.group(1), "mobile2": m.group(2), "mobile3": m.group(3),
            "email1": "", "email2": "",
        })
        if r.text.strip():
            raise AinError("자녀 선택 실패: " + re.sub(r"\s+", " ", r.text.strip())[:200])
        self.child_name = name
        pop = self._get("/reserve/reservePop.do", params={
            "deptCd": "", "deptNo": "", "profEmpNo": "", "receiptNO": ""})
        if self._need_login(pop.text):
            raise SessionExpired()
        mt = re.search(r"infantsTimeArr\s*=\s*\[([^\]]*)\]", pop.text)
        if mt:
            times = re.findall(r"\d{4}", mt.group(1))
            if times:
                self.infant_times = times
        return kid

    # ------------------------------------------------------------------ 조회
    def open_dates(self, doctor_code, yyyy, mm):
        """해당 월에 의료진이 진료하는(예약창 열린) 날짜 목록 ['20261103', ...]"""
        r = self._post("/reserve/step3.do", {
            "deptCd": DEPT_PD, "profEmpNo": doctor_code, "yyyy": str(yyyy), "mm": str(mm)})
        if self._need_login(r.text):
            raise SessionExpired()
        try:
            return json.loads(r.text).get("RSV_DATE", [])
        except ValueError:
            raise AinError("날짜 조회 응답을 해석할 수 없습니다: " + r.text[:100])

    def slots(self, doctor_code, rsv_date, infant=True, infant_ok=True):
        """예약 가능한 시간 목록 ['1000', ...]. 사이트 화면(Step4Result)과 같은 규칙으로 걸러서 돌려준다.

        infant=True  : 영유아검진 시간(infant_times)만
        infant=False : 일반 진료 시간(영유아검진 시간 제외)
        infant_ok    : 영유아검진을 하는 의료진인지 (아니면 영유아 시간 구분을 하지 않는다)
        """
        r = self._post("/reserve/step4.do", {
            "deptCd": DEPT_PD, "profEmpNo": doctor_code, "rsvDate": rsv_date})
        if self._need_login(r.text):
            raise SessionExpired()
        try:
            rows = json.loads(r.text)
        except ValueError:
            raise AinError("시간 조회 응답을 해석할 수 없습니다: " + r.text[:100])

        def hhmm(row):
            return str(row.get("treatmentAvailableTime", "")).replace(":", "")

        saturday = datetime.strptime(rsv_date, "%Y%m%d").weekday() == 5
        std = 1400 if saturday else 1300  # 오전/오후 기준
        times = [hhmm(r_) for r_ in rows]
        am = [t for t in times if int(t) <= std]
        pm = [t for t in times if int(t) > std]

        def minus_hour(t):  # 오전/오후 마지막 시간에서 60분 전까지만 노출
            m = int(t[:2]) * 60 + int(t[2:]) - 60
            return f"{m // 60:02d}{m % 60:02d}"

        am_last = minus_hour(am[-1]) if am else ""
        pm_last = minus_hour(pm[-1]) if pm else ""
        today = datetime.now().strftime("%Y%m%d")
        out = []
        for row in rows:
            t = hhmm(row)
            if str(row.get("appointmentAvailableYn", "")).strip() != "Y" or int(t) > 1900:
                continue
            if rsv_date == today and int(datetime.now().strftime("%Y%m%d%H%M")) + 120 > int(rsv_date + t):
                continue  # 당일은 2시간 이후부터
            if int(t[2:]) % 10 != 0 or 1300 <= int(t) <= 1350:  # 10분 단위, 점심시간 제외
                continue
            if infant_ok and ((t in self.infant_times) != infant):
                continue
            if (am_last and int(t) <= std and int(am_last) < int(t)) or \
                    (pm_last and int(t) > std and int(pm_last) < int(t)):
                continue
            out.append(t)
        return sorted(out)

    def my_reservations(self):
        """내 예약 현황 [{child, dept, doctor, date, time, infant}] (마이아인 > 예약 내역)"""
        html = self._get("/mypage/reserveList.do").text
        if self._need_login(html):
            raise SessionExpired()
        out = []
        for tr in re.findall(r"<tr>([\s\S]*?)</tr>", html):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip()
                     for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr)]
            m = re.search(r"reserveCheck\(\s*'CANCEL',\s*'[^']*',\s*'(\d{8})',\s*'(\d{4})'", tr)
            if len(cells) >= 6 and m:
                out.append({"child": cells[2], "dept": cells[3], "doctor": cells[4],
                            "date": m.group(1), "time": m.group(2), "infant": "영유아검진" in cells[3]})
        return out

    # ------------------------------------------------------------------ 예약
    def book(self, doctor_code, rsv_date, rsv_time, infant=True):
        """예약 확정(영유아검진/일반 진료). 성공하면 True, 실패하면 서버 메시지를 AinError로."""
        r = self._post("/reserve/sendReservation.do", {
            "deptCd": DEPT_PD, "profEmpNo": doctor_code, "rsvDate": rsv_date,
            "rsvTime": rsv_time, "infantYn": "Y" if infant else "N"})
        if self._need_login(r.text):
            raise SessionExpired()
        res = r.text.strip()
        if res == "Y":
            return True
        raise AinError(res or "알 수 없는 오류(빈 응답)")
