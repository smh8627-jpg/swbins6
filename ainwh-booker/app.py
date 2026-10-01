"""아인병원 영유아검진 자동예약 - 데스크톱 앱 (customtkinter, MIT)."""
import calendar
import ctypes
import os
import queue
import re
import threading
import tkinter as tk
from datetime import date, datetime

import customtkinter as ctk

import config
from ainwh_client import DEFAULT_DOCTORS, DEFAULT_INFANT_TIMES, DOCTORS, AinClient, AinError
from monitor import Monitor, next_open

try:
    import winsound
except ImportError:  # 비 Windows 환경
    winsound = None

FONT = "Malgun Gothic"
C_BG, C_CARD, C_CARD2 = "#0b1220", "#141c2f", "#1b2540"
C_TEXT, C_MUTED = "#e6ebf5", "#8b97b3"
C_ACCENT, C_ACCENT_H = "#6366f1", "#4f46e5"
C_OK, C_WARN, C_BAD = "#22c55e", "#f59e0b", "#ef4444"
WEEK = ["월", "화", "수", "목", "금", "토"]


def f(size=13, bold=False):
    return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")


class Card(ctk.CTkFrame):
    def __init__(self, master, title, icon=""):
        super().__init__(master, fg_color=C_CARD, corner_radius=14)
        self.columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=f"{icon}  {title}".strip(), font=f(14, True), text_color=C_TEXT,
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.body.columnconfigure(0, weight=1)


class DatePicker(ctk.CTkFrame):
    """원하는 날짜를 눌러서 여러 개 고르는 달력. 지난 날짜는 선택할 수 없다."""

    def __init__(self, master, on_change=None):
        super().__init__(master, fg_color="transparent")
        self.on_change = on_change
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected, self.open_days = set(), set()
        self.enabled = True
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=0, column=0, sticky="ew")
        nav.columnconfigure(1, weight=1)
        self.btn_prev = ctk.CTkButton(nav, text="◀", width=36, height=30, fg_color=C_CARD2, hover_color="#26314f",
                                      command=lambda: self._move(-1))
        self.btn_prev.grid(row=0, column=0)
        self.lbl_month = ctk.CTkLabel(nav, text="", font=f(14, True), text_color=C_TEXT)
        self.lbl_month.grid(row=0, column=1)
        self.btn_next = ctk.CTkButton(nav, text="▶", width=36, height=30, fg_color=C_CARD2, hover_color="#26314f",
                                      command=lambda: self._move(1))
        self.btn_next.grid(row=0, column=2)
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=1, column=0, pady=(8, 0))
        for i, w in enumerate("일월화수목금토"):
            ctk.CTkLabel(grid, text=w, width=40, font=f(11), text_color=C_BAD if i == 0 else C_MUTED).grid(
                row=0, column=i)
        self.cells = []
        for r in range(6):
            row = []
            for c in range(7):
                b = ctk.CTkButton(grid, text="", width=40, height=32, corner_radius=8, font=f(12),
                                  text_color_disabled="#3d4866")
                b.grid(row=r + 1, column=c, padx=2, pady=2)
                row.append(b)
            self.cells.append(row)
        self.lbl_sel = ctk.CTkLabel(self, text="", font=f(12), text_color=C_MUTED, wraplength=360, justify="left")
        self.lbl_sel.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.btn_clear = ctk.CTkButton(self, text="모두 해제", width=90, height=28, fg_color=C_CARD2,
                                       hover_color="#26314f", font=f(12), command=self.clear)
        self.btn_clear.grid(row=3, column=0, sticky="e", pady=(6, 0))
        self._render()

    # --- 외부 인터페이스
    def get_dates(self):
        return sorted(self.selected)

    def set_dates(self, dates):
        today = date.today().isoformat()
        self.selected = {d for d in dates if d > today}
        if self.selected:
            first = date.fromisoformat(min(self.selected))
            self.year, self.month = first.year, first.month
        self._render()

    def set_open_days(self, days):
        if set(days) != self.open_days:
            self.open_days = set(days)
            self._render()

    def set_enabled(self, flag):
        self.enabled = flag
        self._render()

    def clear(self):
        self.selected.clear()
        self._render()
        if self.on_change:
            self.on_change()

    # --- 내부
    def _move(self, delta):
        today = date.today()
        idx = self.year * 12 + self.month - 1 + delta
        lo, hi = today.year * 12 + today.month - 1, today.year * 12 + today.month - 1 + 12
        if lo <= idx <= hi:
            self.year, self.month = idx // 12, idx % 12 + 1
            self._render()

    def _toggle(self, iso):
        if not self.enabled:
            return
        self.selected.symmetric_difference_update({iso})
        self._render()
        if self.on_change:
            self.on_change()

    def _render(self):
        today = date.today()
        self.lbl_month.configure(text=f"{self.year}년 {self.month}월")
        cur = today.year * 12 + today.month - 1
        idx = self.year * 12 + self.month - 1
        self.btn_prev.configure(state="normal" if self.enabled and idx > cur else "disabled")
        self.btn_next.configure(state="normal" if self.enabled and idx < cur + 12 else "disabled")
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(self.year, self.month)
        for r in range(6):
            for c in range(7):
                b = self.cells[r][c]
                day = weeks[r][c] if r < len(weeks) else 0
                if not day:
                    b.configure(text="", state="disabled", fg_color="transparent", hover=False)
                    continue
                d = date(self.year, self.month, day)
                iso = d.isoformat()
                if d <= today:
                    b.configure(text=str(day), state="disabled", fg_color="transparent", hover=False)
                    continue
                if iso in self.selected:
                    fg, tc, hv = C_ACCENT, "#ffffff", C_ACCENT_H
                elif iso in self.open_days:
                    fg, tc, hv = "#123524", C_OK, "#1a4a32"
                else:
                    fg, tc, hv = C_CARD2, C_TEXT, "#26314f"
                b.configure(text=str(day), state="normal" if self.enabled else "disabled", fg_color=fg,
                            text_color=tc, hover_color=hv, hover=True, command=lambda i=iso: self._toggle(i))
        if self.selected:
            names = "월화수목금토일"
            parts = [f"{x[5:7]}/{x[8:]}({names[date.fromisoformat(x).weekday()]})" for x in sorted(self.selected)]
            self.lbl_sel.configure(text=f"선택한 날짜 {len(parts)}일: " + ", ".join(parts), text_color=C_TEXT)
        else:
            self.lbl_sel.configure(text="달력에서 날짜를 눌러 선택하세요 (여러 날짜 가능)", text_color=C_MUTED)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("영유아검진 자동예약")
        self.geometry("1120x780")
        self.minsize(980, 680)
        self.configure(fg_color=C_BG)

        self.cfg = config.load()
        self.q = queue.Queue()
        self.monitor = None
        self.running = False
        self.found_alerted = set()
        self.checks_text = "0"
        self.last_check = "-"
        self.reservations = []  # 서버에서 확인한 내 예약 목록
        self.override_existing = False  # 사용자가 "다시 예약 활성화"를 누른 경우

        self._build_header()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        body.columnconfigure(0, weight=0, minsize=430)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_settings(body)
        self._build_dashboard(body)
        self._load_into_ui()
        self._refresh_booked()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._pump)
        self.after(500, self._tick)
        self.after(700, self._probe_on_launch)

    # ------------------------------------------------------------------ 헤더
    def _build_header(self):
        h = ctk.CTkFrame(self, fg_color="transparent")
        h.pack(fill="x", padx=24, pady=(18, 12))
        ctk.CTkLabel(h, text="🍼", font=ctk.CTkFont(size=34)).pack(side="left")
        t = ctk.CTkFrame(h, fg_color="transparent")
        t.pack(side="left", padx=12)
        ctk.CTkLabel(t, text="영유아검진 자동예약", font=f(22, True), text_color=C_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(t, text="아인병원 소아청소년과 · 취소 자리를 감시해서 바로 예약합니다",
                     font=f(12), text_color=C_MUTED, anchor="w").pack(anchor="w")
        self.pill = ctk.CTkLabel(h, text="  ● 대기 중  ", font=f(13, True), text_color="#cbd5e1",
                                 fg_color="#26314f", corner_radius=20, height=34)
        self.pill.pack(side="right")

    def _set_pill(self, text, color):
        self.pill.configure(text=f"  ● {text}  ", text_color=color, fg_color=C_CARD2)

    # ------------------------------------------------------------------ 설정(왼쪽)
    def _entry(self, parent, **kw):
        return ctk.CTkEntry(parent, height=36, corner_radius=8, border_width=1, fg_color=C_CARD2,
                            border_color="#2a3558", text_color=C_TEXT, font=f(13), **kw)

    def _build_settings(self, parent):
        sc = ctk.CTkScrollableFrame(parent, fg_color="transparent", scrollbar_button_color="#26314f")
        sc.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        sc.columnconfigure(0, weight=1)
        self.setting_widgets = []

        # 예약 종류
        c = Card(sc, "예약 종류", "🗂️")
        c.pack(fill="x", pady=(0, 12))
        self.seg_mode = ctk.CTkSegmentedButton(
            c.body, values=["영유아검진", "진료 예약"], height=38, font=f(13, True), selected_color=C_ACCENT,
            selected_hover_color=C_ACCENT_H, unselected_color=C_CARD2, unselected_hover_color="#26314f",
            command=lambda _v: self._on_mode_change())
        self.seg_mode.grid(row=0, column=0, sticky="ew")
        self.seg_mode.set("영유아검진")
        self.lbl_mode_note = ctk.CTkLabel(c.body, text="", font=f(11), text_color=C_MUTED, wraplength=370,
                                          justify="left")
        self.lbl_mode_note.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.setting_widgets.append(self.seg_mode)

        # 계정
        c = Card(sc, "계정", "👤")
        c.pack(fill="x", pady=(0, 12))
        b = c.body
        self.e_id = self._entry(b, placeholder_text="아이디")
        self.e_id.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.e_pw = self._entry(b, placeholder_text="비밀번호", show="•")
        self.e_pw.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.btn_check = ctk.CTkButton(b, text="로그인 확인 · 자녀 불러오기", height=36, corner_radius=8,
                                       fg_color=C_CARD2, hover_color="#26314f", font=f(13, True),
                                       command=self._check_login)
        self.btn_check.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.child_var = tk.StringVar(value="")
        self.opt_child = ctk.CTkOptionMenu(b, variable=self.child_var, values=["(로그인 확인 후 선택)"], height=36,
                                           corner_radius=8, fg_color=C_CARD2, button_color="#26314f",
                                           button_hover_color=C_ACCENT, font=f(13), dropdown_font=f(13),
                                           command=lambda _v: self._refresh_booked())
        self.opt_child.grid(row=3, column=0, sticky="ew")
        self.save_var = tk.BooleanVar(value=True)
        self.chk_save = ctk.CTkCheckBox(b, text="계정 저장 (이 PC에만 저장)", variable=self.save_var, font=f(13),
                                        fg_color=C_ACCENT, hover_color=C_ACCENT_H, border_color="#3a4670",
                                        text_color=C_TEXT, command=self._on_save_toggle)
        self.chk_save.grid(row=4, column=0, sticky="w", pady=(10, 0))
        ctk.CTkLabel(b, text="비밀번호는 이 PC의 Windows 계정으로만 풀 수 있게 암호화됩니다. 저장하면 다음 실행 때 "
                             "예약 현황을 바로 확인합니다(감시는 시작 버튼을 눌러야 시작).",
                     font=f(11), text_color=C_MUTED, wraplength=370, justify="left").grid(
            row=5, column=0, sticky="w", pady=(6, 0))
        self.setting_widgets += [self.e_id, self.e_pw, self.btn_check, self.opt_child, self.chk_save]

        # 의료진
        c = Card(sc, "의료진", "🩺")
        c.pack(fill="x", pady=(0, 12))
        b = c.body
        b.columnconfigure((0, 1), weight=1)
        self.doc_vars, self.doc_widgets = {}, {}
        for i, name in enumerate(DOCTORS):
            v = tk.BooleanVar(value=name in DEFAULT_DOCTORS)
            cb = ctk.CTkCheckBox(b, text=name, variable=v, font=f(13), fg_color=C_ACCENT, hover_color=C_ACCENT_H,
                                 border_color="#3a4670", text_color=C_TEXT)
            cb.grid(row=i // 2, column=i % 2, sticky="w", pady=3)
            self.doc_vars[name] = v
            self.doc_widgets[name] = cb
            self.setting_widgets.append(cb)
        self.e_extra = self._entry(b, placeholder_text="추가 의료진  이름=코드, 이름=코드")
        self.e_extra.grid(row=(len(DOCTORS) + 1) // 2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.setting_widgets.append(self.e_extra)

        # 희망 조건
        c = Card(sc, "희망 조건", "📅")
        c.pack(fill="x", pady=(0, 12))
        b = c.body
        b.columnconfigure((0, 2), weight=1)
        ctk.CTkLabel(b, text="예약 희망 기간 (YYYY-MM-DD)", font=f(12), text_color=C_MUTED).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.e_from = self._entry(b)
        self.e_from.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ctk.CTkLabel(b, text="~", text_color=C_MUTED).grid(row=1, column=1, padx=8)
        self.e_to = self._entry(b)
        self.e_to.grid(row=1, column=2, sticky="ew", pady=(4, 10))
        ctk.CTkLabel(b, text="요일", font=f(12), text_color=C_MUTED).grid(row=2, column=0, sticky="w")
        wk = ctk.CTkFrame(b, fg_color="transparent")
        wk.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 10))
        self.wk_vars, self.wk_cbs = [], []
        for i, w in enumerate(WEEK):
            v = tk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(wk, text=w, variable=v, width=50, checkbox_width=18, checkbox_height=18,
                                 font=f(13), fg_color=C_ACCENT, hover_color=C_ACCENT_H, border_color="#3a4670")
            cb.pack(side="left", padx=(0, 6))
            self.wk_vars.append(v)
            self.wk_cbs.append(cb)
            self.setting_widgets.append(cb)
        self.lbl_time = ctk.CTkLabel(b, text="영유아검진 시간", font=f(12), text_color=C_MUTED)
        self.lbl_time.grid(row=4, column=0, sticky="w")
        tm = self.tm_infant = ctk.CTkFrame(b, fg_color="transparent")
        tm.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.tm_general = ctk.CTkFrame(b, fg_color="transparent")
        self.tm_general.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.e_tfrom = self._entry(self.tm_general, width=90, placeholder_text="09:00")
        self.e_tfrom.pack(side="left")
        ctk.CTkLabel(self.tm_general, text="~", text_color=C_MUTED).pack(side="left", padx=8)
        self.e_tto = self._entry(self.tm_general, width=90, placeholder_text="18:00")
        self.e_tto.pack(side="left")
        self.tm_general.grid_remove()
        self.time_vars = {}
        for t in DEFAULT_INFANT_TIMES:
            v = tk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(tm, text=f"{t[:2]}:{t[2:]}", variable=v, width=64, checkbox_width=18,
                                 checkbox_height=18, font=f(13), fg_color=C_ACCENT, hover_color=C_ACCENT_H,
                                 border_color="#3a4670")
            cb.pack(side="left", padx=(0, 6))
            self.time_vars[t] = v
            self.setting_widgets.append(cb)
        self.setting_widgets += [self.e_from, self.e_to, self.e_tfrom, self.e_tto]

        # 원하는 날짜 선택
        c = Card(sc, "원하는 날짜 선택", "🗓️")
        c.pack(fill="x", pady=(0, 12))
        b = c.body
        self.sw_pick = ctk.CTkSwitch(b, text="선택한 날짜에서만 예약", font=f(13), progress_color=C_OK,
                                     command=self._apply_pick)
        self.sw_pick.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(b, text="켜면 위의 기간·요일 대신 달력에서 고른 날짜만 감시하고 예약합니다. "
                             "여러 날짜를 고르면 그중 가장 빠른 날짜부터 예약합니다.",
                     font=f(11), text_color=C_MUTED, wraplength=370, justify="left").grid(
            row=1, column=0, sticky="w", pady=(6, 8))
        self.picker = DatePicker(b)
        self.picker.grid(row=2, column=0, sticky="ew")
        self.picker.grid_remove()
        self.setting_widgets.append(self.sw_pick)

        # 동작
        c = Card(sc, "동작", "⚙️")
        c.pack(fill="x", pady=(0, 12))
        b = c.body
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text="확인 주기", font=f(13), text_color=C_TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_interval = ctk.CTkLabel(row, text="45초", font=f(13, True), text_color=C_ACCENT)
        self.lbl_interval.grid(row=0, column=1, sticky="e")
        self.sl_interval = ctk.CTkSlider(b, from_=15, to=300, number_of_steps=57, progress_color=C_ACCENT,
                                         button_color=C_ACCENT, button_hover_color=C_ACCENT_H,
                                         command=lambda v: self.lbl_interval.configure(text=f"{int(v)}초"))
        self.sl_interval.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        self.sw_auto = ctk.CTkSwitch(b, text="빈자리가 나면 자동으로 예약", font=f(13), progress_color=C_OK)
        self.sw_auto.grid(row=2, column=0, sticky="w", pady=4)
        self.sw_burst = ctk.CTkSwitch(b, text="매월 1일 09:00 전후 집중 감시 (3초)", font=f(13), progress_color=C_OK)
        self.sw_burst.grid(row=3, column=0, sticky="w", pady=4)
        self.sw_exit = ctk.CTkSwitch(b, text="예약이 완료되면 프로그램 종료", font=f(13), progress_color=C_OK)
        self.sw_exit.grid(row=4, column=0, sticky="w", pady=4)
        ctk.CTkLabel(b, text="자동 예약을 끄면 빈자리만 알려줍니다.", font=f(11), text_color=C_MUTED).grid(
            row=5, column=0, sticky="w", pady=(4, 0))
        self.setting_widgets += [self.sl_interval, self.sw_auto, self.sw_burst, self.sw_exit]

    # ------------------------------------------------------------------ 대시보드(오른쪽)
    def _stat(self, parent, col, title):
        c = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=14)
        c.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
        ctk.CTkLabel(c, text=title, font=f(12), text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 0))
        v = ctk.CTkLabel(c, text="-", font=f(20, True), text_color=C_TEXT)
        v.pack(anchor="w", padx=16, pady=(2, 12))
        return v

    def _build_dashboard(self, parent):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.grid(row=0, column=1, sticky="nsew")
        r.columnconfigure(0, weight=1)
        r.rowconfigure(4, weight=1)

        stats = ctk.CTkFrame(r, fg_color="transparent")
        stats.grid(row=0, column=0, sticky="ew")
        stats.columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.st_checks = self._stat(stats, 0, "확인 횟수")
        self.st_last = self._stat(stats, 1, "마지막 확인")
        self.st_open = self._stat(stats, 2, "다음 달 오픈까지")
        self.st_open.configure(font=f(17, True))

        self.banner = ctk.CTkFrame(r, fg_color="#0f2a1d", corner_radius=14, border_width=1, border_color="#1f5a3a")
        self.banner.columnconfigure(0, weight=1)
        self.lbl_booked = ctk.CTkLabel(self.banner, text="", font=f(13, True), text_color=C_OK, anchor="w",
                                       justify="left")
        self.lbl_booked.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        ctk.CTkButton(self.banner, text="다시 예약 활성화", width=140, height=34, corner_radius=8, font=f(13, True),
                      fg_color="#1f5a3a", hover_color="#27734a", command=self._reactivate).grid(
            row=0, column=1, padx=14, pady=12)
        self.banner.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.banner.grid_remove()

        self.btn_run = ctk.CTkButton(r, text="▶  감시 시작", height=52, corner_radius=14, font=f(17, True),
                                     fg_color=C_ACCENT, hover_color=C_ACCENT_H, command=self._toggle)
        self.btn_run.grid(row=2, column=0, sticky="ew", pady=12)

        self.live = ctk.CTkFrame(r, fg_color=C_CARD, corner_radius=14)
        self.live.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.live.columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self.live, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="📡  실시간 예약 현황", font=f(14, True), text_color=C_TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        self.lbl_live_time = ctk.CTkLabel(head, text="", font=f(11), text_color=C_MUTED)
        self.lbl_live_time.grid(row=0, column=1, sticky="e")
        self.live_body = ctk.CTkScrollableFrame(self.live, fg_color="transparent", height=170,
                                                scrollbar_button_color="#26314f")
        self.live_body.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.live_body.columnconfigure(0, weight=1)
        self._live_last = None
        self._live_placeholder("감시를 시작하면 의료진별 날짜 현황이 여기에 실시간으로 표시됩니다.")

        lc = ctk.CTkFrame(r, fg_color=C_CARD, corner_radius=14)
        lc.grid(row=4, column=0, sticky="nsew")
        lc.rowconfigure(1, weight=1)
        lc.columnconfigure(0, weight=1)
        ctk.CTkLabel(lc, text="📋  활동 로그", font=f(14, True), text_color=C_TEXT, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.log_box = ctk.CTkTextbox(lc, fg_color="#0e1627", corner_radius=10, font=ctk.CTkFont(family="Consolas", size=12),
                                      text_color="#c7d2e8", wrap="word", state="disabled")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.log_box.tag_config("ok", foreground=C_OK)
        self.log_box.tag_config("hit", foreground=C_WARN)
        self.log_box.tag_config("bad", foreground=C_BAD)
        self.log_box.tag_config("dim", foreground="#6b7897")

    # ------------------------------------------------------------------ 설정 <-> UI
    def _load_into_ui(self):
        c = self.cfg
        self.save_var.set(c.get("save_login", True))
        if c.get("user_id"):
            self.e_id.insert(0, c["user_id"])
        pw = config.unprotect(c.get("password_enc", ""))
        if pw:
            self.e_pw.insert(0, pw)
        if c.get("child"):
            self.opt_child.configure(values=[c["child"]])
            self.child_var.set(c["child"])
        else:
            self.child_var.set("(로그인 확인 후 선택)")
        for n, v in self.doc_vars.items():
            v.set(n in c["doctors"])
        if c.get("extra_doctors"):
            self.e_extra.insert(0, c["extra_doctors"])
        self.e_from.insert(0, c["date_from"])
        self.e_to.insert(0, c["date_to"])
        for i, v in enumerate(self.wk_vars):
            v.set(i in c["weekdays"])
        for t, v in self.time_vars.items():
            v.set(t in c["times"])
        self.sl_interval.set(c["interval_sec"])
        self.lbl_interval.configure(text=f"{int(c['interval_sec'])}초")
        (self.sw_auto.select if c["auto_book"] else self.sw_auto.deselect)()
        (self.sw_burst.select if c["burst"] else self.sw_burst.deselect)()
        (self.sw_exit.select if c["exit_on_success"] else self.sw_exit.deselect)()
        self.seg_mode.set("영유아검진" if c.get("visit_type", "infant") == "infant" else "진료 예약")
        tf, tt = c.get("time_from", "0900"), c.get("time_to", "1800")
        self.e_tfrom.insert(0, f"{tf[:2]}:{tf[2:]}")
        self.e_tto.insert(0, f"{tt[:2]}:{tt[2:]}")
        (self.sw_pick.select if c.get("date_mode") == "pick" else self.sw_pick.deselect)()
        self.picker.set_dates(c.get("picked_dates", []))
        self._apply_mode()
        self._apply_pick()

    def _collect(self):
        """UI → cfg. 잘못된 값이면 ValueError."""
        uid, pw = self.e_id.get().strip(), self.e_pw.get()
        if not uid or not pw:
            raise ValueError("아이디와 비밀번호를 입력하세요.")
        child = self.child_var.get().strip()
        if not child or child.startswith("("):
            raise ValueError("'로그인 확인 · 자녀 불러오기'로 자녀를 선택하세요.")
        pick = bool(self.sw_pick.get())
        picked = self.picker.get_dates()
        if pick and not picked:
            raise ValueError("달력에서 예약할 날짜를 한 개 이상 선택하세요.")
        try:
            d1, d2 = date.fromisoformat(self.e_from.get().strip()), date.fromisoformat(self.e_to.get().strip())
        except ValueError:
            if not pick:
                raise ValueError("희망 기간은 YYYY-MM-DD 형식으로 입력하세요.")
            d1, d2 = date.fromisoformat(self.cfg["date_from"]), date.fromisoformat(self.cfg["date_to"])
        if d2 < d1 and not pick:
            raise ValueError("희망 기간의 종료일이 시작일보다 빠릅니다.")
        if d2 < d1:
            d1, d2 = date.fromisoformat(self.cfg["date_from"]), date.fromisoformat(self.cfg["date_to"])
        visit = self._visit()
        docs = [n for n, v in self.doc_vars.items() if v.get() and (visit == "general" or DOCTORS[n][1])]
        extra = self.e_extra.get().strip()
        if not docs and not extra:
            raise ValueError("의료진을 한 명 이상 선택하세요.")
        weekdays = [i for i, v in enumerate(self.wk_vars) if v.get()]
        times = [t for t, v in self.time_vars.items() if v.get()]
        t_from, t_to = self._parse_hhmm(self.e_tfrom.get()), self._parse_hhmm(self.e_tto.get())
        if not weekdays and not pick:
            raise ValueError("요일을 한 개 이상 선택하세요.")
        if visit == "infant" and not times:
            raise ValueError("영유아검진 시간을 한 개 이상 선택하세요.")
        if visit == "general" and not (t_from and t_to and t_from <= t_to):
            raise ValueError("진료 시간대는 HH:MM 형식으로, 시작이 종료보다 빨라야 합니다.")
        self.cfg.update({
            "user_id": uid if self.save_var.get() else "",
            "password_enc": config.protect(pw) if self.save_var.get() else "",
            "save_login": bool(self.save_var.get()), "child": child, "doctors": docs,
            "extra_doctors": extra, "date_from": d1.isoformat(), "date_to": d2.isoformat(),
            "weekdays": weekdays, "times": times, "interval_sec": int(self.sl_interval.get()),
            "auto_book": bool(self.sw_auto.get()), "burst": bool(self.sw_burst.get()),
            "exit_on_success": bool(self.sw_exit.get()), "visit_type": visit,
            "date_mode": "pick" if pick else "range", "picked_dates": picked,
            "time_from": t_from or self.cfg["time_from"], "time_to": t_to or self.cfg["time_to"],
        })
        config.save(self.cfg)
        return pw

    def _persist_account(self):
        """계정(아이디/비밀번호)과 자녀만 바로 저장한다. '계정 저장'을 끄면 저장된 계정을 지운다."""
        if self.save_var.get():
            uid, pw, child = self.e_id.get().strip(), self.e_pw.get(), self.child_var.get().strip()
            if uid and pw:
                self.cfg["user_id"], self.cfg["password_enc"] = uid, config.protect(pw)
            if child and not child.startswith("("):
                self.cfg["child"] = child
        else:
            self.cfg["user_id"], self.cfg["password_enc"] = "", ""
        self.cfg["save_login"] = bool(self.save_var.get())
        config.save(self.cfg)

    def _on_save_toggle(self):
        self._persist_account()
        self._log("계정 저장을 켰습니다." if self.save_var.get() else "저장된 계정을 삭제했습니다.", "dim")

    # ------------------------------------------------------------------ 로그인 확인
    def _check_login(self):
        uid, pw = self.e_id.get().strip(), self.e_pw.get()
        if not uid or not pw:
            self._log("아이디와 비밀번호를 입력하세요.", "bad")
            return
        self.btn_check.configure(state="disabled", text="확인 중...")

        self._probe(uid, pw, announce=True)

    def _probe_on_launch(self):
        """프로그램을 켜면 예약 현황만 조회한다(읽기 전용). 감시는 '감시 시작' 버튼을 눌러야 시작."""
        uid, pw = self.e_id.get().strip(), self.e_pw.get()
        child = self.child_var.get()
        if uid and pw and child and not child.startswith("("):
            self._log("예약 현황을 확인하는 중…", "dim")
            self._probe(uid, pw, announce=False)

    def _probe(self, uid, pw, announce):
        def work():
            try:
                c = AinClient()
                c.login(uid, pw)
                kids = [k["name"] for k in c.list_children()]
                res = c.my_reservations()
                self.q.put(("probe", kids, res, announce))
            except AinError as e:
                self.q.put(("log", f"[로그인 확인] {e}", "bad"))
            except Exception as e:
                self.q.put(("log", f"[로그인 확인] 네트워크 오류: {e.__class__.__name__}", "bad"))
            self.q.put(("check_done",))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 실시간 현황
    def _live_clear(self):
        for w in self.live_body.winfo_children():
            w.destroy()

    def _live_placeholder(self, text):
        self._live_clear()
        ctk.CTkLabel(self.live_body, text=text, font=f(12), text_color=C_MUTED).grid(row=0, column=0, pady=30)

    def _on_snapshot(self, snap, when):
        self.lbl_live_time.configure(text=f"마지막 갱신 {when:%H:%M:%S}")
        self.picker.set_open_days({f"{ds[:4]}-{ds[4:6]}-{ds[6:]}" for rows in snap.values()
                                   for ds, _, match in rows if match})
        sig = repr(snap)
        if sig == self._live_last:  # 변화가 없으면 다시 그리지 않는다
            return
        self._live_last = sig
        self._live_clear()
        week = "월화수목금토일"
        row = 0
        for name, rows in snap.items():
            n_open = sum(1 for _, _, match in rows if match)
            head = ctk.CTkFrame(self.live_body, fg_color="transparent")
            head.grid(row=row, column=0, sticky="ew", pady=(8, 4))
            ctk.CTkLabel(head, text=name, font=f(13, True), text_color=C_TEXT).pack(side="left")
            ctk.CTkLabel(head, text=f"  빈자리 {n_open}일" if n_open else "  전부 마감",
                         font=f(12, True), text_color=C_OK if n_open else C_MUTED).pack(side="left")
            row += 1
            chips = ctk.CTkFrame(self.live_body, fg_color="transparent")
            chips.grid(row=row, column=0, sticky="w")
            row += 1
            if not rows:
                ctk.CTkLabel(chips, text="조건에 맞는 진료일 없음", font=f(11), text_color=C_MUTED).grid(row=0, column=0)
            for i, (ds, avail, match) in enumerate(rows):
                wd = week[date(int(ds[:4]), int(ds[4:6]), int(ds[6:])).weekday()]
                label = f"{ds[4:6]}/{ds[6:]}({wd})"
                if match:
                    label += "  " + " ".join(f"{t[:2]}:{t[2:]}" for t in match)
                ctk.CTkLabel(chips, text=label, font=f(11, bool(match)), corner_radius=8, height=26,
                             text_color="#06210f" if match else "#7d89a8",
                             fg_color=C_OK if match else "#1b2540").grid(
                    row=i // 6, column=i % 6, padx=3, pady=3, sticky="w")

    # ------------------------------------------------------------------ 예약 종류
    def _visit(self):
        return "infant" if self.seg_mode.get() == "영유아검진" else "general"

    @staticmethod
    def _parse_hhmm(text):
        m = re.fullmatch(r"(\d{1,2}):?(\d{2})", text.strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        return f"{int(m.group(1)):02d}{m.group(2)}"

    def _apply_mode(self):
        infant = self._visit() == "infant"
        for name, cb in self.doc_widgets.items():  # 영유아검진은 영유아검진 의료진만 선택 가능
            cb.configure(state="normal" if (not infant or DOCTORS[name][1]) else "disabled")
        if infant:
            self.lbl_time.configure(text="영유아검진 시간")
            self.tm_general.grid_remove()
            self.tm_infant.grid()
            note = "영유아검진 전용 시간(10·11·12·15시)만 조회하고 예약합니다."
        else:
            self.lbl_time.configure(text="진료 시간대 (HH:MM ~ HH:MM)")
            self.tm_infant.grid_remove()
            self.tm_general.grid()
            note = ("일반 진료 시간(영유아검진 시간 제외)을 조회합니다. 진료 예약이 있으면 영유아검진 예약이 "
                    "불가능할 수 있습니다. 진료는 자리가 많아서, '자동 예약'을 켜면 시작 즉시 가장 빠른 시간이 예약됩니다.")
        self.lbl_mode_note.configure(text=note)

    def _apply_pick(self):
        pick = bool(self.sw_pick.get())
        if pick:
            self.picker.grid()
        else:
            self.picker.grid_remove()
        for w in [self.e_from, self.e_to, *self.wk_cbs]:  # 날짜 선택 중에는 기간/요일 설정을 쓰지 않는다
            w.configure(state="disabled" if pick else "normal")

    def _on_mode_change(self):
        self.override_existing = False
        self._apply_mode()
        self._refresh_booked()

    # ------------------------------------------------------------------ 예약 완료 상태
    @staticmethod
    def _fmt(rec):
        d, t = rec["date"], rec["time"]
        return f"{rec['doctor']}  {d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:]}"

    def _match_existing(self):
        child, infant = self.child_var.get(), self._visit() == "infant"
        return next((r for r in self.reservations if r["child"] == child
                     and r["dept"].startswith("소아청소년과") and r["infant"] == infant), None)

    def _booked_for_child(self):
        return None if self.override_existing else self._match_existing()

    def _add_reservation(self, rec):
        key = lambda r: (r["child"], r["date"], r["time"], r["doctor"])
        if not any(key(r) == key(rec) for r in self.reservations):
            self.reservations.append(rec)

    def _refresh_booked(self):
        b = self._booked_for_child()
        if b and not self.running:
            self.lbl_booked.configure(
                text=f"✔ 이미 예약되어 있습니다  {b['child']}  ·  {'영유아검진' if b['infant'] else '진료'}  ·  {self._fmt(b)}"
                     + chr(10) + "예약이 있으면 감시하지 않습니다. 다시 감시하려면 오른쪽 버튼을 누르세요.")
            self.banner.grid()
            self.btn_run.configure(state="disabled", text="이미 예약됨", fg_color="#26314f")
        else:
            self.banner.grid_remove()
            if not self.running:
                self.btn_run.configure(state="normal", text="▶  감시 시작", fg_color=C_ACCENT, hover_color=C_ACCENT_H)

    def _reactivate(self):
        self.override_existing = True
        self._log("다시 예약을 활성화했습니다. '감시 시작'을 누르세요.", "ok")
        self._refresh_booked()

    # ------------------------------------------------------------------ 시작/중지
    def _toggle(self):
        if self.running:
            self._log("중지 요청…", "dim")
            if self.monitor:
                self.monitor.stop()
            self.btn_run.configure(state="disabled", text="중지하는 중…")
            return
        try:
            pw = self._collect()
        except ValueError as e:
            self._log(str(e), "bad")
            return
        if self._booked_for_child():
            self._log("이미 예약이 있는 자녀입니다. '다시 예약 활성화'를 눌러야 다시 감시할 수 있습니다.", "bad")
            return
        self.monitor = Monitor(
            {**self.cfg, "user_id": self.e_id.get().strip(), "ignore_existing": self.override_existing}, pw,
            log=lambda m: self.q.put(("log", m, "")),
            on_status=lambda s: self.q.put(("status", s)),
            on_found=lambda fl: self.q.put(("found", fl)),
            on_booked=lambda b: self.q.put(("booked", b)),
            on_stopped=lambda: self.q.put(("stopped",)),
            on_snapshot=lambda snap, when: self.q.put(("snap", snap, when)),
            on_existing=lambda rec: self.q.put(("existing", rec)),
        )
        self.found_alerted.clear()
        self.running = True
        self._lock_settings(True)
        self.btn_run.configure(text="■  감시 중지", fg_color="#b42318", hover_color="#912018")
        mode = "자동 예약" if self.cfg["auto_book"] else "알림만"
        kind = "영유아검진" if self.cfg["visit_type"] == "infant" else "진료 예약"
        span = (f"선택한 날짜 {len(self.cfg['picked_dates'])}일" if self.cfg["date_mode"] == "pick"
                else f"{self.cfg['date_from']} ~ {self.cfg['date_to']}")
        self._log(f"감시 시작 · {kind} · {mode} · {span} · "
                  f"{self.cfg['interval_sec']}초 간격", "ok")
        try:  # 감시 중 절전 방지
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass
        self.monitor.start()

    def _lock_settings(self, lock):
        for w in self.setting_widgets:
            try:
                w.configure(state="disabled" if lock else "normal")
            except Exception:
                pass
        self.picker.set_enabled(not lock)
        if not lock:
            self._apply_mode()
            self._apply_pick()

    # ------------------------------------------------------------------ 큐 처리
    def _pump(self):
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "log":
                    self._log(m[1], m[2] if len(m) > 2 else "")
                elif kind == "status":
                    self._on_status(m[1])
                elif kind == "kids":
                    self._on_kids(m[1])
                elif kind == "check_done":
                    self.btn_check.configure(state="normal", text="로그인 확인 · 자녀 불러오기")
                elif kind == "found":
                    self._on_found(m[1])
                elif kind == "booked":
                    self._on_booked(m[1])
                elif kind == "probe":
                    self._on_probe(m[1], m[2], m[3])
                elif kind == "existing":
                    self._add_reservation(m[1])
                elif kind == "snap":
                    self._on_snapshot(m[1], m[2])
                elif kind == "stopped":
                    self._on_stopped()
        except queue.Empty:
            pass
        self.after(150, self._pump)

    def _on_probe(self, kids, reservations, announce):
        if announce:
            self._on_kids(kids)
            self._persist_account()
        child = self.child_var.get()
        self.reservations = list(reservations)
        kind = "영유아검진" if self._visit() == "infant" else "진료"
        found = self._match_existing()
        if found:
            self._log(f"[예약 현황] 이미 예약됨({kind}): {self._fmt(found)}", "ok")
        else:
            self._log(f"[예약 현황] {child or '자녀'}님의 {kind} 예약 없음", "dim")
        self._refresh_booked()

    def _on_kids(self, kids):
        if not kids:
            self._log("로그인은 되었지만 등록된 자녀가 없습니다. 병원(032-247-2000)에 가족등록을 요청하세요.", "bad")
            return
        self.opt_child.configure(values=kids)
        cur = self.child_var.get()
        self.child_var.set(cur if cur in kids else kids[0])
        self._log(f"로그인 확인 완료 · 자녀: {', '.join(kids)}", "ok")

    def _on_status(self, s):
        color = {"감시 중": C_OK, "예약 시도 중": C_WARN}.get(s, C_WARN)
        self._set_pill(s, color)
        if s == "감시 중":
            self.checks_text = str(self.monitor.checks if self.monitor else 0)
            self.st_checks.configure(text=self.checks_text)
            self.st_last.configure(text=datetime.now().strftime("%H:%M:%S"))

    def _on_found(self, fresh):
        self._beep(2)
        if self.cfg["auto_book"]:
            return
        new = [x for x in fresh if x[:3] not in self.found_alerted]
        if not new:
            return
        self.found_alerted.update(x[:3] for x in new)
        lines = "\n".join(f"{n}  {d[:4]}-{d[4:6]}-{d[6:]}  {t[:2]}:{t[2:]}" for d, t, n, _ in new[:8])
        self._popup("빈자리가 났습니다", lines + "\n\n사이트에서 바로 예약하세요.", C_WARN)

    def _on_booked(self, b):
        d, t, name = b
        infant = self.cfg.get("visit_type", "infant") == "infant"
        self._add_reservation({"child": self.cfg["child"], "doctor": name, "date": d, "time": t, "infant": infant,
                               "dept": "소아청소년과(영유아검진)" if infant else "소아청소년과"})
        exiting = self.cfg.get("exit_on_success")
        tail = "확인을 누르면 프로그램이 종료됩니다." if exiting else "감시는 종료되었습니다."
        self._beep(6)
        self._popup(f"{'영유아검진' if infant else '진료'} 예약 완료 🎉",
                    f"{name}\n{d[:4]}-{d[4:6]}-{d[6:]}  {t[:2]}:{t[2:]}\n\n"
                    f"마이아인 > 예약 내역에서 꼭 확인하세요.\n{tail}", C_OK,
                    on_close=self._on_close if exiting else None)

    def _on_stopped(self):
        self.running = False
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        self._lock_settings(False)
        self.btn_run.configure(state="normal", text="▶  감시 시작", fg_color=C_ACCENT, hover_color=C_ACCENT_H)
        self._set_pill("대기 중", "#cbd5e1")
        self._log("감시 종료", "dim")
        self._refresh_booked()

    # ------------------------------------------------------------------ 보조
    def _popup(self, title, text, color, on_close=None):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("420x260")
        win.configure(fg_color=C_CARD)
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=title, font=f(20, True), text_color=color).pack(pady=(26, 10))
        ctk.CTkLabel(win, text=text, font=f(14), text_color=C_TEXT, justify="center").pack(pady=4, padx=20)
        def close():
            win.destroy()
            if on_close:
                on_close()

        win.protocol("WM_DELETE_WINDOW", close)
        ctk.CTkButton(win, text="확인", width=120, height=38, fg_color=C_ACCENT, hover_color=C_ACCENT_H,
                      command=close).pack(side="bottom", pady=22)
        win.after(200, win.lift)

    def _beep(self, n):
        def run():
            for _ in range(n):
                if winsound:
                    winsound.Beep(1200, 220)
                threading.Event().wait(0.12)
        threading.Thread(target=run, daemon=True).start()

    def _log(self, msg, tag=""):
        if not tag:
            tag = "ok" if msg.startswith("✔") else "hit" if msg.startswith("★") else \
                "bad" if msg.startswith(("[중단]", "[오류]", "예약 실패")) else \
                "dim" if msg.startswith(("빈자리 없음", "[네트워크]")) else ""
        line = f"{datetime.now():%m-%d %H:%M:%S}  {msg}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line, tag or None)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        try:
            os.makedirs(config.APP_DIR, exist_ok=True)
            with open(config.LOG_PATH, "a", encoding="utf-8") as fp:
                fp.write(line)
        except OSError:
            pass

    def _tick(self):
        left = next_open() - datetime.now()
        d, rem = left.days, left.seconds
        txt = f"{d}일 {rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}" if d else \
            f"{rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}"
        self.st_open.configure(text=txt)
        self.after(1000, self._tick)

    def _on_close(self):
        if self.monitor:
            self.monitor.stop()
        try:
            self._collect()
        except ValueError:
            self._persist_account()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
