# -*- coding: utf-8 -*-
"""
TEPS 350+ 대비 30일 단어 암기 프로그램 (로컬 GUI)

흐름: 단어 크게 표시 → (스스로 뜻 떠올림) → [뜻 보기] 실제 뜻·예문·발음 →
      [자가평가 1/2/3] → Leitner 간격 반복 → 진행 저장.

- 데이터:  vocab.json  (parse_vocab.py 로 생성)
- 저장:    progress.json (단어별 박스/마스터/통계, 이어하기)
- 오디오:  gTTS(mp3 캐시) → 실패 시 pyttsx3(오프라인) 폴백
"""
import os, sys, json, re, threading, hashlib, queue, random
from datetime import datetime, timezone
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

BASE = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = os.path.join(BASE, "vocab.json")
PROGRESS_PATH = os.path.join(BASE, "progress.json")
EXAMPLES_PATH = os.path.join(BASE, "examples.json")
AUDIO_DIR = os.path.join(BASE, "audio_cache")
NUM_DAYS = 30
REVIEW_CAP = 200         # 홈 '복습 세션'에서 한 번에 담는 약한 단어 최대 수

# ---------- 색상/테마 (화이트/라이트) ----------
BG = "#f4f5f7"          # 페이지 배경 (연회색)
CARD = "#ffffff"        # 카드/패널 (흰색)
FG = "#1f2937"          # 기본 글자 (진한 슬레이트)
SUB = "#6b7280"         # 보조 글자 (회색)
ACCENT = "#2563eb"      # 파랑
GOOD = "#16a34a"        # 초록
WARN = "#d97706"        # 주황
BAD = "#dc2626"         # 빨강
DONE = "#dcfce7"        # 완료 타일 배경 (연초록)
BTN_FG = "#ffffff"      # 색 버튼 위 글자(흰색)
MUTED = "#eef1f5"       # 연회색 컨트롤/버튼
BORDER = "#d7dbe0"      # 옅은 테두리
TILE_PROG = "#dbeafe"   # 진행중 타일 배경 (연파랑)
DONE_FG = "#166534"     # 완료 타일 글자 (진초록)
IMG_BG = "#eef4ff"      # 감각 이미지 박스 배경 (연파랑)
IMG_FG = "#1e3a8a"      # 감각 이미지 글자 (진남색)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# =========================================================
#  오디오 매니저
# =========================================================
class AudioManager:
    def __init__(self, status_cb=None):
        os.makedirs(AUDIO_DIR, exist_ok=True)
        self.status_cb = status_cb          # 상태 문구를 GUI로 전달(옵션)
        self._mixer_ok = False
        self._init_err = ""
        try:
            import pygame
            pygame.mixer.init()
            self._pygame = pygame
            self._mixer_ok = True
        except Exception as e:
            self._pygame = None
            self._init_err = repr(e)[:120]
        self._lock = threading.Lock()

    def _status(self, msg):
        if self.status_cb:
            try:
                self.status_cb(msg)
            except Exception:
                pass

    def _cache_path(self, text):
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
        safe = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:30] or "w"
        return os.path.join(AUDIO_DIR, f"{safe}_{h}.mp3")

    def play(self, text):
        """비차단 재생. gTTS 캐시(mp3) 우선, 실패 시 pyttsx3(오프라인)."""
        t = threading.Thread(target=self._play_worker, args=(text,), daemon=True)
        t.start()

    def _play_worker(self, text):
        path = self._cache_path(text)
        # 1) 캐시 없으면 gTTS 생성 시도(네트워크). 8초 타임아웃으로 멈춤 방지.
        if not os.path.exists(path):
            self._status("🔊 발음 준비 중…")
            try:
                import socket
                from gtts import gTTS
                old = socket.getdefaulttimeout()
                socket.setdefaulttimeout(8)
                try:
                    gTTS(text=text, lang="en").save(path)
                finally:
                    socket.setdefaulttimeout(old)
            except Exception:
                # 온라인 실패 → 오프라인 TTS
                self._status("🔈 오프라인 음성")
                if self._speak_offline(text):
                    self._status("")
                else:
                    self._status("⚠ 발음 재생 실패 (인터넷/오디오 확인)")
                return
        # 2) mp3 재생 (pygame)
        if self._mixer_ok:
            try:
                with self._lock:
                    self._pygame.mixer.music.stop()
                    self._pygame.mixer.music.load(path)
                    self._pygame.mixer.music.set_volume(1.0)
                    self._pygame.mixer.music.play()
                self._status("🔊 재생 중")
                return
            except Exception:
                pass
        # 3) mixer 불가 → 오프라인 폴백
        if self._speak_offline(text):
            self._status("")
        else:
            note = "⚠ 발음 재생 실패"
            if not self._mixer_ok:
                note += " (오디오 장치 없음)"
            self._status(note)

    def _speak_offline(self, text):
        """Windows 내장 음성(SAPI5). 워커 스레드에서는 COM 초기화가 필요."""
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pythoncom = None
            try:
                import pyttsx3
                eng = pyttsx3.init()
                eng.setProperty("rate", 150)
                eng.say(text)
                eng.runAndWait()
                eng.stop()
                return True
            finally:
                if pythoncom:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
        except Exception:
            return False

    def diagnose(self):
        """실제 재생을 시도하고 각 단계 결과를 dict로 돌려준다(소리 테스트용)."""
        import time
        res = {"mixer": self._mixer_ok, "mixer_err": self._init_err,
               "network": None, "mp3_played": False, "offline_ok": None, "play_err": ""}
        p = self._cache_path("test")
        try:
            if not os.path.exists(p):
                try:
                    import socket
                    from gtts import gTTS
                    old = socket.getdefaulttimeout(); socket.setdefaulttimeout(8)
                    try:
                        gTTS(text="test", lang="en").save(p)
                    finally:
                        socket.setdefaulttimeout(old)
                    res["network"] = True
                except Exception as e:
                    res["network"] = False
                    res["play_err"] = repr(e)[:100]
                    p = None
            else:
                res["network"] = "cached"
            if p and self._mixer_ok:
                self._pygame.mixer.music.load(p)
                self._pygame.mixer.music.set_volume(1.0)
                self._pygame.mixer.music.play()
                time.sleep(0.4)
                res["mp3_played"] = bool(self._pygame.mixer.music.get_busy())
                time.sleep(1.2)  # 테스트음 끝까지 재생
        except Exception as e:
            res["play_err"] = (res["play_err"] + " | " + repr(e)[:100]).strip(" |")
        res["offline_ok"] = self._speak_offline("test")
        return res


# =========================================================
#  데이터 / 진행상황
# =========================================================
class Store:
    def __init__(self):
        with open(VOCAB_PATH, encoding="utf-8") as f:
            self.vocab = json.load(f)
        # 단어별 고유 key(소문자) + Day 배정
        self.by_key = {}
        self.day_of = {}
        n = len(self.vocab)
        self.per_day = -(-n // NUM_DAYS)  # ceil
        for i, e in enumerate(self.vocab):
            key = e["word"].lower()
            e["key"] = key
            self.by_key[key] = e
            day = min(NUM_DAYS, i // self.per_day + 1)
            e["day"] = day
            self.day_of[key] = day
        self.days = {d: [e for e in self.vocab if e["day"] == d] for d in range(1, NUM_DAYS + 1)}
        self._load_images()
        self._load_examples()
        self.progress = self._load_progress()

    def _load_images(self):
        """images.json(단어key -> 감각 이미지 설명) 오버레이. vocab 재파싱해도 유지됨."""
        path = os.path.join(BASE, "images.json")
        imgs = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    imgs = json.load(f)
            except Exception:
                imgs = {}
        # by_key는 소문자 중복 시 하나만 남으므로 vocab 전체를 순회해야 누락이 없다
        for e in self.vocab:
            img = imgs.get(e["key"])
            e["image"] = img if isinstance(img, str) else ""

    def _load_examples(self):
        """examples.json(단어key -> {"s": 빈칸 예문, "t": 해석, "d": 오답 3개}) 오버레이."""
        exs = {}
        if os.path.exists(EXAMPLES_PATH):
            try:
                with open(EXAMPLES_PATH, encoding="utf-8") as f:
                    exs = json.load(f)
            except Exception:
                exs = {}
        for e in self.vocab:
            v = exs.get(e["key"])
            ok = (isinstance(v, dict) and isinstance(v.get("s"), str) and "____" in v["s"]
                  and isinstance(v.get("d"), list) and len(v["d"]) >= 3)
            e["ex"] = v if ok else None

    def _load_progress(self):
        if os.path.exists(PROGRESS_PATH):
            try:
                with open(PROGRESS_PATH, encoding="utf-8") as f:
                    p = json.load(f)
                p.setdefault("words", {})
                p.setdefault("day_stats", {})
                p.setdefault("last_day", None)
                p.setdefault("active_session", None)
                return p
            except Exception:
                pass
        return {"version": 1, "words": {}, "day_stats": {}, "last_day": None,
                "active_session": None}

    def set_active(self, data):
        self.progress["active_session"] = data
        self.save()

    def clear_active(self):
        self.progress["active_session"] = None
        self.save()

    def save(self):
        tmp = PROGRESS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PROGRESS_PATH)

    # --- word record helpers ---
    def rec(self, key):
        w = self.progress["words"].get(key)
        if w is None:
            w = {"box": 0, "mastered": False, "ever3": False, "seen": 0, "last_seen": None}
            self.progress["words"][key] = w
        return w

    def apply_eval(self, key, score, day):
        w = self.rec(key)
        w["seen"] += 1
        w["last_seen"] = now_iso()
        prev = w["box"]
        w["box"] = score
        if score == 3:
            w["ever3"] = True
            if prev == 3:
                w["mastered"] = True
        else:
            w["mastered"] = False
        ds = self.progress["day_stats"].setdefault(str(day), {"1": 0, "2": 0, "3": 0})
        ds[str(score)] += 1
        self.save()

    # --- stats ---
    def mastered_count(self):
        return sum(1 for w in self.progress["words"].values() if w.get("mastered"))

    def day_progress(self, day):
        """(본 단어수, 전체) — 한 번이라도 평가(box>0)했으면 '봤음'."""
        keys = [e["key"] for e in self.days[day]]
        done = sum(1 for k in keys if self.progress["words"].get(k, {}).get("box", 0) > 0)
        return done, len(keys)

    def day_completed(self, day):
        d, t = self.day_progress(day)
        return t > 0 and d == t

    def reset_day(self, day):
        """이 Day의 학습 기록을 전부 초기화(단어들을 '안 본' 상태로)."""
        for e in self.days[day]:
            self.progress["words"].pop(e["key"], None)
        self.progress["day_stats"].pop(str(day), None)
        act = self.progress.get("active_session")
        if act and act.get("day") == day:
            self.progress["active_session"] = None
        self.save()

    def due_reviews(self, before_day=None, cap=REVIEW_CAP):
        """복습 대상: 학습했으나 마스터 안 됨(box 1/2). before_day 지정 시 그 이전 Day만."""
        items = []
        for key, w in self.progress["words"].items():
            if w.get("mastered"):
                continue
            if w.get("box", 0) not in (1, 2):
                continue
            e = self.by_key.get(key)
            if e is None:
                continue
            if before_day is not None and e["day"] >= before_day:
                continue
            items.append((w.get("box", 2), w.get("last_seen") or "", e))
        # box1(약함) 먼저, 그 다음 오래된 것 먼저
        items.sort(key=lambda x: (x[0], x[1]))
        return [e for _, _, e in items[:cap]]

    def total_due(self):
        return len(self.due_reviews(before_day=None, cap=10 ** 9))

    def day_weak(self, day):
        """이 Day에서 마지막 평가가 모름/애매(box 1/2)인 단어들."""
        return [e for e in self.days[day]
                if self.progress["words"].get(e["key"], {}).get("box", 0) in (1, 2)]


# =========================================================
#  학습 세션
# =========================================================
class Session:
    """한 번에 한 단어씩, '앞으로 한 번씩만' 진행. 재등장(requeue) 없음.
    Day 세션 = 아직 한 번도 안 본(미평가) 새 단어만. 모름/애매로 찍어도 다시 안 나옴.
    복습은 홈의 '🔁 복습 세션'에서만(별도)."""
    def __init__(self, store, day, title, queue, new_keys, kind="day",
                 evals=None, current=None, total_planned=None):
        self.store = store
        self.day = day
        self.title = title
        self.kind = kind                             # "day"(이어하기 대상) | "review" | "replay"
        self.queue = list(queue)                     # 앞으로 볼 단어(순서대로, 한 번씩)
        self.new_keys = set(new_keys)                # 이 Day 신규 단어 전체(요약/진행용)
        self.evals = evals or {1: 0, 2: 0, 3: 0}
        self.current = current                       # 현재 화면에 떠 있는 단어(아직 미평가)
        self.total_planned = (total_planned if total_planned is not None
                              else len(self.queue) + (1 if current else 0))

    @staticmethod
    def _unseen(store, key):
        """아직 한 번도 평가 안 한(box 0/기록 없음) 단어인가."""
        return store.progress["words"].get(key, {}).get("box", 0) == 0

    # ---- 생성자들 ----
    @classmethod
    def for_day(cls, store, day):
        """이 Day 단어 중 '아직 한 번도 안 본' 것만, 순서대로. 복습 단어는 절대 섞지 않음."""
        unseen = [e["key"] for e in store.days[day] if cls._unseen(store, e["key"])]
        new_all = [e["key"] for e in store.days[day]]
        return cls(store, day, f"Day {day}", unseen, new_all, kind="day")

    @classmethod
    def for_day_all(cls, store, day):
        """Day 전체 다시보기 — 이미 본 단어 포함 그 Day 모든 단어를 처음부터."""
        allk = [e["key"] for e in store.days[day]]
        return cls(store, day, f"Day {day} 다시보기", allk, allk, kind="replay")

    @classmethod
    def for_review(cls, store):
        """홈에서 명시적으로 누를 때만. box 1/2(모름·애매) 단어를 한 번씩."""
        reviews = [e["key"] for e in store.due_reviews(before_day=None, cap=REVIEW_CAP)]
        day = store.progress.get("last_day") or 1
        return cls(store, day, "복습", reviews, [], kind="review")

    @classmethod
    def for_words(cls, store, day, entries, title):
        keys = [e["key"] for e in entries]
        return cls(store, day, title, keys, keys, kind="review")

    @classmethod
    def restore(cls, store, d):
        """이어하기 복원(항상 kind=day). 저장된 큐에서 '이미 본 단어/복습 단어'는 걸러내 새 단어만 남긴다.
        (구버전 active_session에 섞여 있던 복습 단어도 이때 제거됨.)"""
        saved_q = d.get("queue", [])
        cur = d.get("current")
        queue = [k for k in saved_q if cls._unseen(store, k)]
        if cur is not None and not cls._unseen(store, cur):
            cur = None
        return cls(store, d["day"], d.get("title", f"Day {d['day']}"),
                   queue, d.get("new_keys", []), kind="day",
                   evals={int(k): v for k, v in d.get("evals", {}).items()},
                   current=cur, total_planned=d.get("total_planned"))

    def to_dict(self):
        return {"day": self.day, "title": self.title, "kind": self.kind,
                "queue": self.queue, "current": self.current,
                "evals": {str(k): v for k, v in self.evals.items()},
                "new_keys": list(self.new_keys),
                "total_planned": self.total_planned}

    # ---- 진행 ----
    def remaining(self):
        return len(self.queue) + (1 if self.current else 0)

    def counts(self):
        b = {1: 0, 2: 0, 3: 0}
        keys = set(self.queue)
        if self.current:
            keys.add(self.current)
        for k in keys:
            box = self.store.progress["words"].get(k, {}).get("box", 0)
            if box in b:
                b[box] += 1
        return b

    def advance(self):
        """다음 단어를 current 로 꺼낸다. (current 가 이미 있으면 유지 — 이어하기용)"""
        if self.current is not None:
            return self.store.by_key[self.current]
        if not self.queue:
            return None
        self.current = self.queue.pop(0)
        return self.store.by_key[self.current]

    def rate(self, score):
        """평가만 기록하고 다음으로. 재등장 없음 — 한 번 본 단어는 이 세션에 다시 안 나옴."""
        self.store.apply_eval(self.current, score, self.day)
        self.evals[score] += 1
        self.current = None


# =========================================================
#  예문 빈칸 테스트 (TEPS 스타일 4지선다)
# =========================================================
class Quiz:
    """Day 단어의 예문 빈칸 4지선다.
    - 맞힘: 모름/애매(box 1/2)였던 단어는 '알았음(3)'으로 승급
    - 틀림: 이미 학습한 단어(box>0)는 '모름(1)'으로 강등 → 복습 대상
    - 아직 안 본 단어(box 0)는 기록을 건드리지 않음 (Day 학습 큐 유지)
    """
    def __init__(self, store, day, entries, title):
        self.store = store
        self.day = day
        self.title = title
        self.items = [e for e in entries if e.get("ex")]
        random.shuffle(self.items)
        self.pos = 0
        self.correct = 0
        self.wrong = []          # 틀린 entry들
        self.choices = []        # 현재 문제의 보기(단어 문자열 4개)
        self.answered = None     # None=미응답, 아니면 고른 보기 index

    def current(self):
        return self.items[self.pos] if self.pos < len(self.items) else None

    def make_choices(self):
        e = self.current()
        opts = [e["word"]] + list(e["ex"]["d"][:3])
        random.shuffle(opts)
        self.choices = opts
        self.answered = None

    def answer(self, idx):
        """보기 선택. True=정답. 진행 기록도 여기서 갱신."""
        e = self.current()
        ok = self.choices[idx] == e["word"]
        self.answered = idx
        box = self.store.progress["words"].get(e["key"], {}).get("box", 0)
        if ok:
            self.correct += 1
            if box in (1, 2):
                self.store.apply_eval(e["key"], 3, self.day)
        else:
            self.wrong.append(e)
            if box > 0:
                self.store.apply_eval(e["key"], 1, self.day)
        return ok

    def next(self):
        self.pos += 1
        return self.current()


# =========================================================
#  GUI
# =========================================================
class App(tk.Tk):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self._audio_q = queue.Queue()                # 스레드 안전한 오디오 상태 전달
        self.audio = AudioManager(status_cb=self._audio_q.put)
        self.session = None
        self.quiz = None
        self.revealed = False
        self.auto_audio = tk.BooleanVar(value=True)

        self.title("TEPS 30일 단어 암기")
        self.configure(bg=BG)
        self.geometry("860x640")
        self.minsize(760, 560)

        self.f_word = tkfont.Font(family="Segoe UI", size=46, weight="bold")
        self.f_pos = tkfont.Font(family="Malgun Gothic", size=13, slant="italic")
        self.f_mean = tkfont.Font(family="Malgun Gothic", size=22)
        self.f_h1 = tkfont.Font(family="Malgun Gothic", size=22, weight="bold")
        self.f_ui = tkfont.Font(family="Malgun Gothic", size=11)
        self.f_small = tkfont.Font(family="Malgun Gothic", size=10)
        self.f_btn = tkfont.Font(family="Malgun Gothic", size=13, weight="bold")
        self.f_image = tkfont.Font(family="Malgun Gothic", size=13)
        self.f_sent = tkfont.Font(family="Segoe UI", size=17)

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self.bind("<Key>", self._on_key)
        self.show_home()
        self._poll_audio_status()                    # 오디오 상태 큐 폴링(메인 스레드)

    # ---------- 오디오 상태(스레드 안전) ----------
    def _poll_audio_status(self):
        try:
            while True:
                msg = self._audio_q.get_nowait()
                if hasattr(self, "lbl_audio"):
                    try:
                        if self.lbl_audio.winfo_exists():
                            self.lbl_audio.config(text=msg)
                    except Exception:
                        pass
        except queue.Empty:
            pass
        try:
            self.after(150, self._poll_audio_status)
        except Exception:
            pass

    # ---------- 공통 ----------
    def _clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    # ---------- 홈 ----------
    def show_home(self):
        self.session = None
        self.quiz = None
        self._clear()
        c = self.container
        tk.Label(c, text="TEPS 350+  ·  30일 단어 암기", font=self.f_h1,
                 bg=BG, fg=FG).pack(pady=(24, 4))
        total = len(self.store.vocab)
        mastered = self.store.mastered_count()
        due = self.store.total_due()
        sub = f"총 {total}개 단어  ·  마스터 {mastered}개  ·  복습 대기 {due}개  ·  하루 약 {self.store.per_day}개"
        tk.Label(c, text=sub, font=self.f_small, bg=BG, fg=SUB).pack(pady=(0, 12))

        # 이어하기 / 복습 버튼
        top = tk.Frame(c, bg=BG)
        top.pack(pady=(0, 10))
        act = self.store.progress.get("active_session")
        act_left = 0
        if act and act.get("kind", "day") == "day":
            # 이어하기 표시는 '아직 안 본 새 단어' 기준으로만 센다(복습 단어 제외)
            unseen = [k for k in act.get("queue", [])
                      if self.store.progress["words"].get(k, {}).get("box", 0) == 0]
            cur = act.get("current")
            cur_ok = cur is not None and self.store.progress["words"].get(cur, {}).get("box", 0) == 0
            act_left = len(unseen) + (1 if cur_ok else 0)
        if act and act_left > 0:
            self._btn(top, f"▶ 이어하기 ({act.get('title','학습')}, 남은 {act_left}개)",
                      self.resume_active, bg=ACCENT).pack(side="left", padx=6)
        elif self.store.progress.get("last_day"):
            nd = self.store.progress["last_day"]
            self._btn(top, f"▶ 이어서 Day {nd}", lambda: self.start_day(nd),
                      bg=ACCENT).pack(side="left", padx=6)
        if due > 0:
            self._btn(top, f"🔁 복습 세션 ({due}개 약한 단어)", self.start_review,
                      bg=WARN).pack(side="left", padx=6)
        self._btn(top, "🔊 소리 테스트", self.test_audio, bg=MUTED, fg=FG).pack(side="left", padx=6)

        # Day 그리드
        grid = tk.Frame(c, bg=BG)
        grid.pack(pady=10)
        for d in range(1, NUM_DAYS + 1):
            r, col = divmod(d - 1, 6)
            done, tot = self.store.day_progress(d)
            completed = self.store.day_completed(d)
            if completed:
                bg, fgc, label = DONE, DONE_FG, f"Day {d}\n✓ 완료"
            elif done > 0:
                bg, fgc, label = TILE_PROG, ACCENT, f"Day {d}\n{done}/{tot}"
            else:
                bg, fgc, label = CARD, FG, f"Day {d}\n{tot}개"
            b = tk.Button(grid, text=label, width=11, height=2, font=self.f_small,
                          bg=bg, fg=fgc, activebackground=bg, relief="flat",
                          bd=0, cursor="hand2", highlightbackground=BORDER,
                          highlightthickness=1,
                          command=lambda dd=d: self.show_day(dd))
            b.grid(row=r, column=col, padx=5, pady=5)

        # 하단
        bottom = tk.Frame(c, bg=BG)
        bottom.pack(side="bottom", pady=12)
        tk.Label(bottom, text="단어를 보고 뜻을 떠올린 뒤 [뜻 보기] → 1/2/3 자가평가하세요.",
                 font=self.f_small, bg=BG, fg=SUB).pack()

    def _btn(self, parent, text, cmd, bg=ACCENT, fg=BTN_FG):
        return tk.Button(parent, text=text, command=cmd, font=self.f_btn, bg=bg, fg=fg,
                         relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                         activebackground=bg, activeforeground=fg,
                         highlightbackground=BORDER, highlightthickness=1)

    # ---------- Day 상세 ----------
    def show_day(self, day):
        self.session = None
        self.quiz = None
        self._clear()
        c = self.container
        done, tot = self.store.day_progress(day)
        unseen = tot - done
        weak = self.store.day_weak(day)
        n_ex = sum(1 for e in self.store.days[day] if e.get("ex"))
        tk.Label(c, text=f"Day {day}", font=self.f_h1, bg=BG, fg=FG).pack(pady=(22, 2))
        tk.Label(c, text=f"총 {tot}개  ·  본 단어 {done}개  ·  안 본 새 단어 {unseen}개  ·  약한 단어 {len(weak)}개",
                 font=self.f_small, bg=BG, fg=SUB).pack(pady=(0, 16))

        col = tk.Frame(c, bg=BG)
        col.pack()
        start_txt = "▶ 학습 시작" if done == 0 else (f"▶ 이어서 학습 (새 단어 {unseen}개)" if unseen > 0 else "✓ 새 단어 모두 봄")
        b1 = self._btn(col, start_txt, lambda: self.start_day(day), bg=ACCENT)
        b1.pack(fill="x", pady=5)
        if unseen == 0 and tot > 0:
            b1.config(state="disabled", cursor="arrow")
        bq = self._btn(col, f"📝 예문 테스트 ({n_ex}문제)", lambda: self.start_quiz(day), bg=GOOD)
        bq.pack(fill="x", pady=5)
        if n_ex == 0:
            bq.config(state="disabled", cursor="arrow", text="📝 예문 테스트 (데이터 없음)")
        bw = self._btn(col, f"🔁 약한 단어만 다시보기 ({len(weak)}개)",
                       lambda: self.start_day_weak(day), bg=WARN)
        bw.pack(fill="x", pady=5)
        if not weak:
            bw.config(state="disabled", cursor="arrow")
        self._btn(col, "🔁 전체 다시보기 (처음부터)", lambda: self.replay_day(day),
                  bg=WARN).pack(fill="x", pady=5)
        self._btn(col, "📋 전체 단어 보기 (목록)", lambda: self.show_day_list(day),
                  bg=MUTED, fg=FG).pack(fill="x", pady=5)
        self._btn(col, "↺ 이 Day 초기화", lambda: self._reset_day_confirm(day),
                  bg=MUTED, fg=BAD).pack(fill="x", pady=5)
        self._btn(col, "🏠 홈", self.show_home, bg=MUTED, fg=FG).pack(fill="x", pady=(18, 5))

    def _reset_day_confirm(self, day):
        if messagebox.askyesno("Day 초기화",
                               f"Day {day}의 학습 기록(자가평가·진행)을 모두 지우고\n'안 본' 상태로 되돌릴까요?\n\n(단어 데이터는 그대로, 진행 기록만 초기화)"):
            self.store.reset_day(day)
            messagebox.showinfo("초기화 완료", f"Day {day}를 초기화했습니다.")
            self.show_day(day)

    # ---------- Day 전체 단어 목록 ----------
    def show_day_list(self, day):
        self.session = None
        self._clear()
        c = self.container
        head = tk.Frame(c, bg=BG)
        head.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(head, text=f"Day {day} · 전체 단어", font=self.f_ui, bg=BG, fg=ACCENT).pack(side="left")
        tk.Button(head, text="← Day", command=lambda: self.show_day(day), font=self.f_small,
                  bg=MUTED, fg=FG, relief="flat", bd=0, padx=12, pady=2, cursor="hand2",
                  highlightbackground=BORDER, highlightthickness=1).pack(side="right")

        # 선택한 단어 상세 패널(상단 고정)
        detail = tk.Frame(c, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        detail.pack(fill="x", padx=18, pady=(4, 8))
        self.dl_word = tk.Label(detail, text="단어를 누르면 뜻이 여기 나옵니다", font=self.f_mean,
                                bg=CARD, fg=SUB, wraplength=780, justify="left")
        self.dl_word.pack(anchor="w", padx=14, pady=(10, 0))
        self.dl_mean = tk.Label(detail, text="", font=self.f_ui, bg=CARD, fg=FG,
                                wraplength=780, justify="left")
        self.dl_mean.pack(anchor="w", padx=14, pady=(2, 2))
        self.dl_img = tk.Label(detail, text="", font=self.f_small, bg=CARD, fg=IMG_FG,
                               wraplength=780, justify="left")
        self.dl_img.pack(anchor="w", padx=14, pady=(0, 10))

        # 스크롤 가능한 단어 그리드
        wrap = tk.Frame(c, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _wheel(ev):
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-ev.delta / 120), "units")
            except Exception:
                pass
        canvas.bind_all("<MouseWheel>", _wheel)

        box_mark = {0: ("·", SUB), 1: ("모름", BAD), 2: ("애매", WARN), 3: ("앎", GOOD)}
        NCOL = 3
        for i, e in enumerate(self.store.days[day]):
            r, cc = divmod(i, NCOL)
            box = self.store.progress["words"].get(e["key"], {}).get("box", 0)
            mark, mcol = box_mark.get(box, ("·", SUB))
            cell = tk.Frame(inner, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            cell.grid(row=r, column=cc, padx=4, pady=4, sticky="nsew")
            btn = tk.Button(cell, text=f"{i+1}. {e['word']}", font=self.f_small, bg=CARD, fg=FG,
                            relief="flat", bd=0, cursor="hand2", anchor="w",
                            activebackground=TILE_PROG, width=22,
                            command=lambda ee=e: self._show_word_detail(ee))
            btn.pack(side="left", fill="x", expand=True)
            tk.Label(cell, text=mark, font=self.f_small, bg=CARD, fg=mcol).pack(side="right", padx=6)
        for cc in range(NCOL):
            inner.grid_columnconfigure(cc, weight=1)

    def _show_word_detail(self, e):
        pos = (e.get("pos", "") + " ") if e.get("pos") else ""
        self.dl_word.config(text=f"{e['word']}", fg=FG)
        self.dl_mean.config(text=f"{pos}{e.get('meaning','')}")
        img = e.get("image", "")
        self.dl_img.config(text=("🧠  " + img) if img else "")
        self.audio.play(e["word"])

    # ---------- 세션 시작 ----------
    def start_day(self, day):
        self.store.progress["last_day"] = day
        # 이 Day에 진행 중이던 세션이 있으면 '그 지점부터' 이어서 (처음부터 재시작 금지)
        act = self.store.progress.get("active_session")
        if (act and act.get("kind", "day") == "day" and act.get("day") == day
                and (act.get("queue") or act.get("current"))):
            self._begin_session(Session.restore(self.store, act))
        else:
            self._begin_session(Session.for_day(self.store, day))

    def replay_day(self, day):
        self.store.progress["last_day"] = day
        self._begin_session(Session.for_day_all(self.store, day))

    def start_review(self):
        sess = Session.for_review(self.store)
        if sess.remaining() == 0:
            messagebox.showinfo("복습", "복습할 단어가 없습니다!")
            return
        self._begin_session(sess)

    def resume_active(self):
        act = self.store.progress.get("active_session")
        if not act:
            self.show_home(); return
        self._begin_session(Session.restore(self.store, act))

    def start_day_weak(self, day):
        """이 Day에서 모름/애매(box 1/2)인 단어만 카드로 다시보기."""
        weak = self.store.day_weak(day)
        if not weak:
            messagebox.showinfo("약한 단어", "이 Day에는 모름/애매로 남은 단어가 없습니다!")
            return
        self.store.progress["last_day"] = day
        self._begin_session(Session.for_words(self.store, day, weak, f"Day {day} 약한 단어"))

    # ---------- 예문 빈칸 테스트 ----------
    def start_quiz(self, day, entries=None, title=None):
        pool = entries if entries is not None else self.store.days[day]
        quiz = Quiz(self.store, day, pool, title or f"Day {day} 예문 테스트")
        if not quiz.items:
            messagebox.showinfo("예문 테스트", "이 Day의 예문 데이터가 아직 없습니다.")
            return
        self.session = None
        self.quiz = quiz
        self.show_quiz_question()

    def show_quiz_question(self):
        q = self.quiz
        e = q.current()
        if e is None:
            self.show_quiz_summary()
            return
        q.make_choices()
        self._clear()
        c = self.container

        header = tk.Frame(c, bg=BG)
        header.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(header, text=f"{q.title}", font=self.f_ui, bg=BG, fg=ACCENT).pack(side="left")
        tk.Button(header, text="그만두기", command=lambda: self.show_day(q.day), font=self.f_small,
                  bg=MUTED, fg=FG, relief="flat", bd=0, padx=12, pady=2, cursor="hand2",
                  activebackground=MUTED, highlightbackground=BORDER,
                  highlightthickness=1).pack(side="right")
        self.lbl_qprog = tk.Label(header, text="", font=self.f_small, bg=BG, fg=SUB)
        self.lbl_qprog.pack(side="right", padx=12)
        self._update_quiz_header()

        card = tk.Frame(c, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18, pady=10)
        tk.Label(card, text="빈칸에 알맞은 단어는?", font=self.f_small, bg=CARD, fg=SUB).pack(pady=(18, 4))
        tk.Label(card, text=e["ex"]["s"], font=self.f_sent, bg=CARD, fg=FG,
                 wraplength=740, justify="center").pack(padx=24, pady=(0, 10))

        self.lbl_qverdict = tk.Label(card, text="", font=self.f_btn, bg=CARD)
        self.lbl_qverdict.pack(pady=(2, 0))
        self.lbl_qmean = tk.Label(card, text="", font=self.f_ui, bg=CARD, fg=FG,
                                  wraplength=720, justify="center")
        self.lbl_qmean.pack(pady=(2, 0))
        self.lbl_qexpl = tk.Label(card, text="", font=self.f_small, bg=CARD, fg=SUB,
                                  wraplength=720, justify="left")
        self.lbl_qexpl.pack(padx=24, pady=(4, 12))

        controls = tk.Frame(c, bg=BG)
        controls.pack(fill="x", padx=18, pady=(0, 16))
        self.quiz_btns = []
        for j, w in enumerate(q.choices):
            b = tk.Button(controls, text=f"{j+1}.  {w}", font=self.f_btn, bg=CARD, fg=FG,
                          relief="flat", bd=0, pady=10, cursor="hand2", anchor="w", padx=16,
                          activebackground=TILE_PROG, activeforeground=FG,
                          highlightbackground=BORDER, highlightthickness=1,
                          command=lambda jj=j: self._quiz_pick(jj))
            b.pack(fill="x", pady=3)
            self.quiz_btns.append(b)
        self.btn_qnext = tk.Button(controls, text="다음  (Space)", command=self._quiz_next,
                                   font=self.f_btn, bg=ACCENT, fg=BTN_FG, relief="flat",
                                   bd=0, pady=12, cursor="hand2", activebackground=ACCENT,
                                   activeforeground=BTN_FG)
        # btn_qnext 는 답을 고른 뒤에만 pack

    def _update_quiz_header(self):
        q = self.quiz
        answered = q.pos + (1 if q.answered is not None else 0)
        self.lbl_qprog.config(
            text=f"문제 {min(q.pos + 1, len(q.items))}/{len(q.items)}   ·   맞힘 {q.correct} / 틀림 {len(q.wrong)}")

    def _quiz_pick(self, i):
        q = self.quiz
        if q is None or q.answered is not None or q.current() is None or i >= len(q.choices):
            return
        e = q.current()
        ok = q.answer(i)
        ans = e["word"]
        for j, b in enumerate(self.quiz_btns):
            if q.choices[j] == ans:
                b.config(bg=GOOD, fg=BTN_FG)
            elif j == i:
                b.config(bg=BAD, fg=BTN_FG)
            b.config(state="disabled", cursor="arrow")
        self.lbl_qverdict.config(text="⭕ 정답!" if ok else f"❌ 오답 — 정답: {ans}",
                                 fg=GOOD if ok else BAD)
        pos = (e.get("pos", "") + " ") if e.get("pos") else ""
        self.lbl_qmean.config(text=f"{ans}  ·  {pos}{e.get('meaning', '')}")
        expl = e["ex"]["s"].replace("____", ans)
        if e["ex"].get("t"):
            expl += "\n" + e["ex"]["t"]
        self.lbl_qexpl.config(text=expl)
        self.btn_qnext.pack(fill="x", pady=(10, 0))
        self._update_quiz_header()
        if self.auto_audio.get():
            self.audio.play(ans)

    def _quiz_next(self):
        q = self.quiz
        if q is None or q.answered is None:
            return
        q.next()
        self.show_quiz_question()

    def show_quiz_summary(self):
        q = self.quiz
        self._clear()
        c = self.container
        total = len(q.items)
        pct = round(q.correct * 100 / total) if total else 0
        tk.Label(c, text=f"{q.title} 종료 🎉", font=self.f_h1, bg=BG, fg=GOOD).pack(pady=(30, 8))
        tk.Label(c, text=f"{q.correct} / {total}  ({pct}%)", font=self.f_word, bg=BG,
                 fg=GOOD if pct >= 80 else (WARN if pct >= 50 else BAD)).pack(pady=6)
        if q.wrong:
            tk.Label(c, text=f"틀린 단어 {len(q.wrong)}개 — 모름으로 표시되어 복습에 나옵니다",
                     font=self.f_small, bg=BG, fg=SUB).pack(pady=(10, 2))
            preview = ", ".join(e["word"] for e in q.wrong[:15])
            tk.Label(c, text=preview, font=self.f_small, bg=BG, fg=WARN, wraplength=760).pack()
        btns = tk.Frame(c, bg=BG)
        btns.pack(pady=24)
        day, wrong = q.day, list(q.wrong)
        self._btn(btns, "🏠 홈으로", self.show_home, bg=CARD, fg=FG).pack(side="left", padx=6)
        self._btn(btns, f"← Day {day}", lambda: self.show_day(day), bg=MUTED, fg=FG).pack(side="left", padx=6)
        if wrong:
            self._btn(btns, f"📝 틀린 것만 재시험 ({len(wrong)})",
                      lambda: self.start_quiz(day, entries=wrong, title=f"Day {day} 오답 재시험"),
                      bg=WARN).pack(side="left", padx=6)
            self._btn(btns, "🔁 틀린 단어 카드 학습",
                      lambda: self._begin_session(Session.for_words(self.store, day, wrong,
                                                                    f"Day {day} 오답 복습")),
                      bg=ACCENT).pack(side="left", padx=6)

    def test_audio(self):
        """실제로 테스트음을 재생하고 각 단계 결과를 알려준다."""
        self.config(cursor="watch"); self.update()
        r = self.audio.diagnose()
        self.config(cursor="")
        net = {True: "정상", False: "실패(인터넷 안 됨)", "cached": "캐시 사용", None: "?"}.get(r["network"], str(r["network"]))
        lines = [
            f"• 오디오 장치(mixer): {'정상' if r['mixer'] else '실패'}" + (f"  [{r['mixer_err']}]" if r.get("mixer_err") else ""),
            f"• 인터넷(발음 생성): {net}",
            f"• mp3 재생 시도: {'재생됨' if r['mp3_played'] else '재생 안 됨'}",
            f"• 오프라인 음성(pyttsx3): {'가능' if r['offline_ok'] else '불가'}",
        ]
        if r.get("play_err"):
            lines.append(f"• 오류: {r['play_err']}")
        if r["mp3_played"] or r["offline_ok"]:
            head = "방금 테스트음을 재생했습니다.\n소리가 들렸다면 정상입니다. 🎧\n\n"
            tail = "\n\n소리가 안 들렸다면: 시스템 볼륨/음소거, 출력 장치(스피커·헤드폰) 선택을 확인하세요."
        else:
            head = "소리를 낼 수 없습니다. 아래를 확인하세요.\n\n"
            tail = "\n\n(mixer 실패면 오디오 드라이버, 인터넷 실패+오프라인 불가면 pyttsx3 설치 확인)"
        messagebox.showinfo("소리 테스트", head + "\n".join(lines) + tail)

    def _begin_session(self, session):
        self.quiz = None
        self.session = session
        if session.remaining() == 0:
            if session.kind == "day":
                self.store.clear_active()
            messagebox.showinfo("완료", "이 Day의 새 단어를 이미 다 봤습니다!\n(다시 보려면 '전체 다시보기', 틀린 것만 보려면 '복습 세션')")
            self.show_home()
            return
        self.show_study()
        self._save_active()
        self.next_word()

    def _save_active(self):
        # 이어하기 대상은 'Day 학습'뿐. 복습/다시보기는 진행상태를 저장하지 않음.
        if self.session is not None and self.session.kind == "day":
            self.store.set_active(self.session.to_dict())

    # ---------- 학습 화면 ----------
    def show_study(self):
        self._clear()
        c = self.container

        # 헤더
        header = tk.Frame(c, bg=BG)
        header.pack(fill="x", padx=18, pady=(12, 4))
        self.lbl_title = tk.Label(header, text="", font=self.f_ui, bg=BG, fg=ACCENT)
        self.lbl_title.pack(side="left")
        tk.Button(header, text="홈", command=self._confirm_home, font=self.f_small,
                  bg=MUTED, fg=FG, relief="flat", bd=0, padx=12, pady=2, cursor="hand2",
                  activebackground=MUTED, highlightbackground=BORDER,
                  highlightthickness=1).pack(side="right")
        self.lbl_boxes = tk.Label(header, text="", font=self.f_small, bg=BG, fg=SUB)
        self.lbl_boxes.pack(side="right", padx=12)
        tk.Checkbutton(header, text="자동발음", variable=self.auto_audio, font=self.f_small,
                       bg=BG, fg=SUB, selectcolor=CARD, activebackground=BG,
                       activeforeground=FG, bd=0, highlightthickness=0).pack(side="right", padx=8)

        # 진행 바
        self.lbl_prog = tk.Label(c, text="", font=self.f_small, bg=BG, fg=SUB)
        self.lbl_prog.pack()

        # 카드
        card = tk.Frame(c, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18, pady=12)
        self.card = card

        self.lbl_word = tk.Label(card, text="", font=self.f_word, bg=CARD, fg=FG, wraplength=760)
        self.lbl_word.pack(pady=(46, 6))

        self.lbl_hint = tk.Label(card, text="스페이스 = 뜻 보기", font=self.f_small, bg=CARD, fg=SUB)
        self.lbl_hint.pack()

        # 정답 영역(초기 숨김)
        self.answer = tk.Frame(card, bg=CARD)
        self.lbl_pos = tk.Label(self.answer, text="", font=self.f_pos, bg=CARD, fg=ACCENT)
        self.lbl_pos.pack(pady=(10, 0))
        self.lbl_mean = tk.Label(self.answer, text="", font=self.f_mean, bg=CARD, fg=FG,
                                 wraplength=760, justify="center")
        self.lbl_mean.pack(pady=(4, 6))
        # 감각 이미지 박스 (CLAUDE.md '이미지 심기') — image 있을 때만 색이 채워짐
        self.lbl_image = tk.Label(self.answer, text="", font=self.f_image, bg=CARD, fg=IMG_FG,
                                  wraplength=700, justify="left")
        self.lbl_image.pack(fill="x", padx=40, pady=(2, 6))
        self.lbl_ex = tk.Label(self.answer, text="", font=self.f_small, bg=CARD, fg=SUB,
                               wraplength=700, justify="left")
        self.lbl_ex.pack(fill="x", padx=40, pady=(0, 4))
        self.lbl_meta = tk.Label(self.answer, text="", font=self.f_small, bg=CARD, fg=SUB)
        self.lbl_meta.pack()
        audio_row = tk.Frame(self.answer, bg=CARD)
        audio_row.pack(pady=10)
        tk.Button(audio_row, text="🔊 발음 듣기 (P)", command=self._play_audio,
                  font=self.f_ui, bg=MUTED, fg=FG, relief="flat", bd=0,
                  padx=12, pady=6, cursor="hand2", activebackground=MUTED,
                  highlightbackground=BORDER, highlightthickness=1).pack(side="left")
        self.lbl_audio = tk.Label(audio_row, text="", font=self.f_small, bg=CARD, fg=SUB)
        self.lbl_audio.pack(side="left", padx=10)

        # 하단 버튼 영역
        self.controls = tk.Frame(c, bg=BG)
        self.controls.pack(fill="x", padx=18, pady=(0, 16))

        self.btn_reveal = tk.Button(self.controls, text="뜻 보기  (Space)", command=self.reveal,
                                    font=self.f_btn, bg=ACCENT, fg=BTN_FG, relief="flat",
                                    bd=0, pady=12, cursor="hand2", activebackground=ACCENT,
                                    activeforeground=BTN_FG)
        self.btn_reveal.pack(fill="x")

        self.eval_frame = tk.Frame(self.controls, bg=BG)
        specs = [("1  몰랐음", BAD, 1), ("2  애매함", WARN, 2), ("3  알았음", GOOD, 3)]
        self.eval_btns = []
        for txt, col, sc in specs:
            b = tk.Button(self.eval_frame, text=txt, font=self.f_btn, bg=col, fg=BTN_FG,
                          relief="flat", bd=0, pady=12, cursor="hand2",
                          activebackground=col, activeforeground=BTN_FG,
                          command=lambda s=sc: self.rate(s))
            b.pack(side="left", expand=True, fill="x", padx=5)
            self.eval_btns.append(b)

    def next_word(self):
        e = self.session.advance()
        if e is None:
            if self.session.kind == "day":
                self.store.clear_active()
            self.show_summary()
            return
        self.revealed = False
        self.lbl_word.config(text=e["word"])
        self.lbl_hint.config(text="스페이스 = 뜻 보기")
        self.answer.pack_forget()
        self.eval_frame.pack_forget()
        self.btn_reveal.pack(fill="x")
        self._update_header()
        self._save_active()

    def _update_header(self):
        s = self.session
        phase = {"day": "🆕 새 단어", "review": "🔁 복습", "replay": "🔁 다시보기"}.get(s.kind, "")
        self.lbl_title.config(text=f"{s.title}   ·   {phase}")
        self.lbl_prog.config(text=f"남은 {s.remaining()}개   ·   좋음 {s.evals[3]} / 애매 {s.evals[2]} / 모름 {s.evals[1]}")
        b = s.counts()
        self.lbl_boxes.config(text=f"박스  ①{b[1]}  ②{b[2]}  ③{b[3]}")

    def reveal(self):
        if self.revealed or self.session.current is None:
            return
        self.revealed = True
        e = self.store.by_key[self.session.current]
        stars = "★" * e.get("freq", 0)
        srcmap = {"basic_listening": "베이직 리스닝", "basic_reading": "베이직 리딩",
                  "adv_reading": "리딩(고급)"}
        srcs = " · ".join(srcmap.get(s, s) for s in e.get("sources", [e.get("source", "")]))
        self.lbl_pos.config(text=e.get("pos", ""))
        self.lbl_mean.config(text=e.get("meaning", ""))
        meta = srcs
        if stars:
            meta += f"    출제빈도 {stars}"
        self.lbl_meta.config(text=meta)
        img = e.get("image", "")
        if img:
            self.lbl_image.config(text="🧠  " + img, bg=IMG_BG, padx=16, pady=12)
        else:
            self.lbl_image.config(text="", bg=CARD, padx=0, pady=0)
        ex = e.get("ex")
        if ex:
            txt = "✏️  " + ex["s"].replace("____", e["word"])
            if ex.get("t"):
                txt += "\n      " + ex["t"]
            self.lbl_ex.config(text=txt)
        else:
            self.lbl_ex.config(text="")
        self.lbl_hint.config(text="")
        if hasattr(self, "lbl_audio"):
            self.lbl_audio.config(text="")
        self.btn_reveal.pack_forget()
        self.answer.pack(pady=(4, 8))
        self.eval_frame.pack(fill="x")
        if self.auto_audio.get():
            self._play_audio()

    def _play_audio(self):
        if self.session and self.session.current:
            self.audio.play(self.store.by_key[self.session.current]["word"])


    def rate(self, score):
        if not self.revealed or self.session.current is None:
            return
        self.session.rate(score)
        self.next_word()

    # ---------- 요약 ----------
    def show_summary(self):
        self._clear()
        s = self.session
        c = self.container
        tk.Label(c, text=f"{s.title} 세션 종료 🎉", font=self.f_h1, bg=BG, fg=GOOD).pack(pady=(30, 8))

        studied = s.evals[1] + s.evals[2] + s.evals[3]
        tk.Label(c, text=f"이번 세션 평가 횟수: {studied}회",
                 font=self.f_ui, bg=BG, fg=FG).pack(pady=2)

        stat = tk.Frame(c, bg=BG)
        stat.pack(pady=14)
        for label, val, col in [("알았음 3", s.evals[3], GOOD),
                                 ("애매함 2", s.evals[2], WARN),
                                 ("몰랐음 1", s.evals[1], BAD)]:
            box = tk.Frame(stat, bg=CARD)
            box.pack(side="left", padx=10)
            tk.Label(box, text=str(val), font=self.f_word, bg=CARD, fg=col).pack(padx=24, pady=(10, 0))
            tk.Label(box, text=label, font=self.f_small, bg=CARD, fg=SUB).pack(padx=24, pady=(0, 10))

        # Day 단어의 박스 분포 + 약한 단어
        if s.day and s.new_keys:
            done, tot = self.store.day_progress(s.day)
            tk.Label(c, text=f"Day {s.day} 진행: {done}/{tot} 단어 익힘(★3 도달)",
                     font=self.f_ui, bg=BG, fg=FG).pack(pady=(6, 2))

        # 이 세션 관련 약한 단어: box 1/2 인 것
        weak_keys = [k for k in s.new_keys if self.store.progress["words"].get(k, {}).get("box", 0) in (1, 2)]
        if weak_keys:
            tk.Label(c, text=f"아직 약한 단어 {len(weak_keys)}개 (홈의 복습 세션에서 다시 나옵니다)",
                     font=self.f_small, bg=BG, fg=SUB).pack(pady=(8, 2))
            preview = ", ".join(self.store.by_key[k]["word"] for k in weak_keys[:12])
            tk.Label(c, text=preview, font=self.f_small, bg=BG, fg=WARN,
                     wraplength=760).pack()

        btns = tk.Frame(c, bg=BG)
        btns.pack(pady=24)
        self._btn(btns, "🏠 홈으로", self.show_home, bg=CARD, fg=FG).pack(side="left", padx=6)
        if weak_keys:
            self._btn(btns, "🔁 약한 단어 다시", self._retry_weak, bg=WARN).pack(side="left", padx=6)
        if s.day and s.day < NUM_DAYS:
            self._btn(btns, f"Day {s.day+1} →", lambda: self.start_day(s.day + 1),
                      bg=ACCENT).pack(side="left", padx=6)

    def _retry_weak(self):
        s = self.session
        weak = [self.store.by_key[k] for k in s.new_keys
                if self.store.progress["words"].get(k, {}).get("box", 0) in (1, 2)]
        if not weak:
            self.show_home(); return
        self._begin_session(Session.for_words(self.store, s.day, weak, f"Day {s.day} 복습"))

    # ---------- 키/네비 ----------
    def _confirm_home(self):
        self.show_home()

    def _on_key(self, ev):
        k = ev.keysym
        if self.quiz is not None:
            if k in ("1", "2", "3", "4", "KP_1", "KP_2", "KP_3", "KP_4"):
                self._quiz_pick(int(k[-1]) - 1)
            elif k in ("space", "Return"):
                self._quiz_next()
            return
        if self.session is None:
            return
        if k in ("space", "Return"):
            if not self.revealed:
                self.reveal()
        elif k in ("1", "2", "3", "KP_1", "KP_2", "KP_3"):
            self.rate(int(k[-1]))
        elif k.lower() == "p":
            if self.revealed:
                self._play_audio()


def main():
    if not os.path.exists(VOCAB_PATH):
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("데이터 없음",
                             "vocab.json 이 없습니다.\n먼저 parse_vocab.py 를 실행하세요.")
        return
    store = Store()
    app = App(store)
    app.mainloop()


if __name__ == "__main__":
    main()
