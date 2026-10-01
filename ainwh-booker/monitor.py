"""영유아검진 빈자리 감시 + 자동 예약 루프."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests

import config
from ainwh_client import DOCTORS, AinClient, AinError, SessionExpired

BURST_START = (8, 59, 50)  # 매월 1일 09:00 오픈 직전부터
BURST_END = (9, 6, 0)
BURST_INTERVAL = 3.0
FAIL_COOLDOWN = timedelta(minutes=10)


def parse_extra(text):
    out = {}
    for part in (text or "").replace("\n", ",").split(","):
        if "=" in part:
            n, c = part.split("=", 1)
            if n.strip() and c.strip().isdigit():
                out[n.strip()] = c.strip()
    return out


def next_open(now=None):
    """다음 달 1일 09:00"""
    now = now or datetime.now()
    y, m = (now.year + (now.month // 12), now.month % 12 + 1)
    cand = datetime(now.year, now.month, 1, 9, 0)
    return cand if cand > now else datetime(y, m, 1, 9, 0)


def in_burst(now=None):
    now = now or datetime.now()
    if now.day != 1:
        return False
    t = (now.hour, now.minute, now.second)
    return BURST_START <= t <= BURST_END


class Monitor:
    def __init__(self, cfg, password, log, on_status, on_found, on_booked, on_stopped, on_snapshot=None,
                 on_existing=None):
        self.cfg, self.password = cfg, password
        self.log, self.on_status = log, on_status
        self.on_found, self.on_booked, self.on_stopped = on_found, on_booked, on_stopped
        self.on_snapshot = on_snapshot
        self.on_existing = on_existing
        self.stop_ev = threading.Event()
        self.thread = None
        self.checks = 0
        self.failed = {}  # (의사, 날짜, 시간) -> 실패 시각
        self.fail_count = 0
        self.notified = set()

    # --------------------------------------------------------------- 제어
    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()

    def _sleep(self, sec):
        self.stop_ev.wait(sec)

    # --------------------------------------------------------------- 내부
    def _infant(self):
        return self.cfg.get("visit_type", "infant") == "infant"

    def _doctors(self):
        pool = {n: v[0] for n, v in DOCTORS.items()}
        extras = parse_extra(self.cfg.get("extra_doctors"))
        pool.update(extras)
        # 영유아검진은 영유아검진 가능 의료진만, 일반 진료는 누구나
        sel = [n for n in self.cfg["doctors"]
               if n in pool and (not self._infant() or n not in DOCTORS or DOCTORS[n][1])]
        sel += [n for n in extras if n not in sel]  # 추가 의료진은 체크 없이도 대상
        return [(n, pool[n]) for n in sel]

    def _picked(self):
        """달력에서 고른 날짜 모드면 날짜 집합(iso), 아니면 None"""
        if self.cfg.get("date_mode") != "pick":
            return None
        return {x for x in self.cfg.get("picked_dates", [])}

    def _months(self):
        picked = self._picked()
        if picked is not None:
            today = date.today()
            ds = [date.fromisoformat(x) for x in picked]
            return sorted({(d.year, d.month) for d in ds if d > today})[:6]
        d1 = date.fromisoformat(self.cfg["date_from"])
        d2 = date.fromisoformat(self.cfg["date_to"])
        today = date.today()
        cur = date(max(d1, today).year, max(d1, today).month, 1)
        out = []
        while cur <= d2 and len(out) < 6:
            out.append((cur.year, cur.month))
            cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
        return out

    def _login(self):
        c = AinClient()
        c.login(self.cfg["user_id"], self.password)
        c.select_child(self.cfg["child"])
        return c

    def _scan_doctor(self, client, name, code, months, d1, d2, weekdays, times):
        """-> (희망 조건에 맞는 빈자리 목록, 화면 표시용 [(날짜, 전체 빈 시간, 조건에 맞는 빈 시간)])"""
        found, rows = [], []
        today = date.today()
        infant = self._infant()
        picked = self._picked()
        infant_ok = DOCTORS[name][1] if name in DOCTORS else True
        t_from, t_to = self.cfg.get("time_from", "0900"), self.cfg.get("time_to", "1800")
        for y, m in months:
            if self.stop_ev.is_set():
                break
            for ds in client.open_dates(code, y, m):
                d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
                if d <= today:
                    continue
                if picked is not None:
                    if d.isoformat() not in picked:
                        continue
                elif not (d1 <= d <= d2) or d.weekday() not in weekdays:
                    continue
                avail = client.slots(code, ds, infant=infant, infant_ok=infant_ok)
                if infant:
                    match = [t for t in avail if t in times]
                else:
                    match = [t for t in avail if t_from <= t <= t_to]
                rows.append((ds, avail, match))
                found += [(ds, t, name, code) for t in match]
                time.sleep(0.05)
        return found, rows

    def _cycle(self, client):
        cfg = self.cfg
        d1, d2 = date.fromisoformat(cfg["date_from"]), date.fromisoformat(cfg["date_to"])
        weekdays, times = set(cfg["weekdays"]), set(cfg["times"])
        months, docs = self._months(), self._doctors()
        with ThreadPoolExecutor(max_workers=max(1, len(docs))) as ex:
            futs = [ex.submit(self._scan_doctor, client, n, c, months, d1, d2, weekdays, times) for n, c in docs]
            results = [f.result() for f in futs]
        found = [x for fnd, _ in results for x in fnd]
        if self.on_snapshot:
            self.on_snapshot({n: rows for (n, _), (_, rows) in zip(docs, results)}, datetime.now())
        order = {n: i for i, (n, _) in enumerate(docs)}
        found.sort(key=lambda x: (x[0], x[1], order[x[2]]))
        return found

    def _run(self):
        try:
            self._loop()
        except Exception as e:  # 예상 못한 오류도 화면에 남긴다
            self.log(f"[오류] 감시가 중단되었습니다: {e!r}")
        finally:
            self.on_stopped()

    def _existing(self, client):
        """이미 같은 자녀의 영유아검진 예약이 있으면 그 예약을 돌려준다."""
        try:
            for r in client.my_reservations():
                if (r["child"] == self.cfg["child"] and r["dept"].startswith("소아청소년과")
                        and r["infant"] == self._infant()):
                    return r
        except AinError as e:  # 조회 실패는 치명적이지 않다 (감시는 계속)
            self.log(f"[경고] 예약 현황 조회 실패: {e}")
        return None

    def _loop(self):
        client = None
        backoff = 10
        self.on_status("로그인 중")
        while not self.stop_ev.is_set():
            try:
                if client is None:
                    client = self._login()
                    backoff = 10
                    self.log(f"로그인 완료 · 자녀 {self.cfg['child']} · 영유아 시간 {','.join(client.infant_times)}")
                    if not self.cfg.get("ignore_existing"):
                        rec = self._existing(client)
                        if rec:
                            self.log(f"[중단] 이미 예약되어 있습니다: {rec['doctor']} {rec['date'][:4]}-"
                                     f"{rec['date'][4:6]}-{rec['date'][6:]} {rec['time'][:2]}:{rec['time'][2:]}")
                            if self.on_existing:
                                self.on_existing(rec)
                            return
                self.on_status("감시 중")
                found = self._cycle(client)
                self.checks += 1
                if found:
                    if self._handle_found(client, found):
                        return
                else:
                    self.log("빈자리 없음")
            except SessionExpired:
                self.log("세션 만료 → 다시 로그인합니다")
                client = None
                continue
            except AinError as e:
                if client is None:  # 로그인/자녀선택 실패는 재시도하지 않는다 (5회 실패 시 계정 제한)
                    self.log(f"[중단] {e}")
                    return
                self.log(f"[경고] {e}")
            except requests.RequestException as e:
                self.log(f"[네트워크] {e.__class__.__name__} — {backoff}초 후 재시도")
                client = None
                self.on_status("네트워크 재시도")
                self._sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            interval = BURST_INTERVAL if (self.cfg.get("burst") and in_burst()) else float(self.cfg["interval_sec"])
            self._sleep(max(1.0, interval))

    def _handle_found(self, client, found):
        """빈자리를 알리고(자동 예약이면 예약 시도). 예약 성공 또는 중단 사유가 있으면 True."""
        now = datetime.now()
        def cooling(f):
            failed_at = self.failed.get((f[2], f[0], f[1]))
            return failed_at is not None and now - failed_at < FAIL_COOLDOWN

        fresh = [f for f in found if not cooling(f)]
        new = [(ds, t, name) for ds, t, name, _ in fresh if (name, ds, t) not in self.notified]
        self.notified.update((name, ds, t) for ds, t, name in new)
        if new:
            head = ", ".join(f"{name} {ds[4:6]}/{ds[6:]} {t[:2]}:{t[2:]}" for ds, t, name in new[:4])
            more = f" 외 {len(new) - 4}건" if len(new) > 4 else ""
            self.log(f"★ 빈자리 {len(new)}건: {head}{more}")
        if fresh:
            self.on_found(fresh)
        if not self.cfg.get("auto_book") or not fresh:
            return False
        for ds, t, name, code in fresh:
            self.on_status("예약 시도 중")
            try:
                client.book(code, ds, t, infant=self._infant())
            except SessionExpired:
                raise
            except AinError as e:
                self.fail_count += 1
                self.failed[(name, ds, t)] = datetime.now()
                self.log(f"예약 실패({name} {ds} {t}): {e}")
                if self.fail_count >= int(self.cfg.get("max_failures", 5)):
                    self.log("[중단] 예약 실패가 반복되어 중단합니다. 사이트에서 직접 확인하세요.")
                    return True
                continue
            self.log(f"✔ 예약 성공: {name} {ds[:4]}-{ds[4:6]}-{ds[6:]} {t[:2]}:{t[2:]}")
            self.on_booked((ds, t, name))
            return True
        return False
