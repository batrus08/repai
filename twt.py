#!/usr/bin/env python3
"""Bot balas X/Twitter dengan Playwright.

Fitur utama:
* Soft-scan: memindai DOM tanpa reload setiap siklus.
* Smart refresh: reload hanya saat stagnan atau dipaksa.
* Health-check sesi otomatis & login resilient.
* Prioritas kandidat berdasarkan waktu.
* Dashboard Rich 5 kolom dengan kontrol interaktif.
* Anti duplikasi balasan menggunakan log id atomik.
* Melewati tweet yang tidak bisa dibalas (balasan ditutup).
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
from logging.handlers import RotatingFileHandler
import sys
import tempfile
import time
import unicodedata
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from urllib.parse import quote

from playwright.async_api import TimeoutError, async_playwright

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner

from ai import classify_text

console = Console()

# ---------------- Konfigurasi & util dasar -----------------

CONFIG_PATH = "bot_config.json"
REPLIED_LOG = "replied_ids.json"
SESSION_DIR = "bot_session"
COOKIE_FILE = "session.json"

DEFAULT_SCAN = {
    "scan_interval_ms": 1500,
    "no_new_cycles_before_refresh": 6,
    "max_age_hours": 3,
}

DEFAULT_NETWORK = {
    "timeout_ms": 15000,
    "max_retries": 3,
    "retry_backoff_ms": 1200,
}

DEFAULT_REPLY = {
    "click_timeout_ms": 2500,
    "composer_timeout_ms": 3000,
    "submit_timeout_ms": 4000,
    "dry_run": False,
}

DEFAULT_DASHBOARD = {
    "compact": True,
    "show_url": True,
    "interactive_keys": True,
}

DEFAULT_LOGGING = {
    "level": "INFO",
    "file": "logs/bot.log",
    "max_bytes": 1_048_576,  # 1 MB
    "backup_count": 3,
    "event_file": "logs/events.jsonl",
    "error_file": "logs/error.log",
}

# Default search URL akan dibangun ulang dari config
SEARCH_URL = "https://x.com/search?q=chatgpt%20%23zonauang&src=recent_search_click&f=live"
LOGIN_URL = "https://x.com/i/flow/login"

AI_CACHE_TTL_MS = 86_400_000  # 24 jam
AI_CACHE: Dict[str, Tuple[str, int]] = {}

EVENT_JOURNAL: "EventJournal" | None = None
EARLY_EVENTS: List[Dict[str, Any]] = []

ENV_FILE = ".env"


class EventJournal:
    """Sederhana: mencatat event ke file JSONL agar mudah ditelusuri."""

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=_json_default)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")


def load_env() -> None:
    """Load key-value pairs from ENV_FILE into environment variables."""
    if not os.path.exists(ENV_FILE):
        log_event("env_missing", level=logging.DEBUG, path=ENV_FILE)
        return
    loaded = 0
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    os.environ.setdefault(key, val)
                    loaded += 1
        log_event("env_loaded", level=logging.DEBUG, path=ENV_FILE, entries=loaded)
    except OSError as exc:
        log_exception("env_load_failed", exc, path=ENV_FILE)


def ensure_api_key() -> bool:
    """Pastikan OPENAI_API_KEY tersedia; jika tidak, minta user dan simpan."""
    if os.getenv("OPENAI_API_KEY"):
        log_event("api_key_available", source="env")
        return True
    try:
        from getpass import getpass

        key = getpass("Masukkan OPENAI_API_KEY: ").strip()
    except Exception:
        key = input("Masukkan OPENAI_API_KEY: ").strip()
    if not key:
        console.print("[bold red]OPENAI_API_KEY diperlukan untuk AI.[/]")
        log_event("api_key_missing_input", level=logging.WARNING)
        return False
    env_vars: Dict[str, str] = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env_vars[k] = v
    env_vars["OPENAI_API_KEY"] = key
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    os.environ["OPENAI_API_KEY"] = key
    console.print("[green]OPENAI_API_KEY tersimpan ke .env[/]")
    log_event("api_key_saved", path=ENV_FILE)
    return True




def load_json(path: str) -> Optional[Any]:
    if not os.path.exists(path):
        log_event("json_missing", level=logging.DEBUG, path=path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        log_event("json_loaded", level=logging.DEBUG, path=path)
        return data
    except json.JSONDecodeError as exc:
        log_exception("json_decode_error", exc, path=path)
        return None


def load_config() -> Dict[str, Any]:
    cfg = load_json(CONFIG_PATH)
    if not isinstance(cfg, dict):
        console.print(f"[bold red]ERROR:[/] Gagal baca `{CONFIG_PATH}`")
        sys.exit(1)

    cfg["scan"] = {**DEFAULT_SCAN, **cfg.get("scan", {})}
    cfg["network"] = {**DEFAULT_NETWORK, **cfg.get("network", {})}
    cfg["reply"] = {**DEFAULT_REPLY, **cfg.get("reply", {})}
    cfg["dashboard"] = {**DEFAULT_DASHBOARD, **cfg.get("dashboard", {})}
    cfg["logging"] = {**DEFAULT_LOGGING, **cfg.get("logging", {})}
    log_event("config_loaded", search=cfg.get("search_config", {}))
    return cfg


def setup_logging(log_cfg: Dict[str, Any]) -> None:
    """Siapkan sistem log ke file & konsol dengan rotasi otomatis."""
    level_name = str(log_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = log_cfg.get("file", DEFAULT_LOGGING["file"])
    event_file = log_cfg.get("event_file", DEFAULT_LOGGING["event_file"])
    error_file = log_cfg.get("error_file", DEFAULT_LOGGING["error_file"])
    max_bytes = int(log_cfg.get("max_bytes", DEFAULT_LOGGING["max_bytes"]))
    backup_count = int(log_cfg.get("backup_count", DEFAULT_LOGGING["backup_count"]))

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler = RotatingFileHandler(
        error_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    stream_handler = logging.StreamHandler()
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for handler in (file_handler, stream_handler, error_handler):
        handler.setFormatter(fmt)
        handler.setLevel(level if handler is not error_handler else logging.WARNING)

    logging.basicConfig(level=level, handlers=[file_handler, stream_handler, error_handler], force=True)
    global EVENT_JOURNAL
    EVENT_JOURNAL = EventJournal(event_file)
    if EARLY_EVENTS:
        for entry in EARLY_EVENTS:
            EVENT_JOURNAL.append(entry)
        EARLY_EVENTS.clear()
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.info(
        "Log sistem aktif di %s (level=%s)", os.path.abspath(log_file), logging.getLevelName(level)
    )
    log_event(
        "logging_initialized",
        log_file=os.path.abspath(log_file),
        error_file=os.path.abspath(error_file),
        event_file=os.path.abspath(event_file),
        level=logging.getLevelName(level),
    )


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj.total_seconds())
    return str(obj)


def _coerce_log_level(level: Any) -> int:
    """Normalize log level input menjadi integer valid untuk logging."""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        level = level.strip()
        if level.isdigit():
            return int(level)
        candidate = getattr(logging, level.upper(), None)
        if isinstance(candidate, int):
            return candidate
    return logging.INFO


def log_event(event: str, *, level: int | str = logging.INFO, **fields: Any) -> None:
    """Catat event ke log biasa dan jurnal JSONL terpisah."""
    level = _coerce_log_level(level)
    timestamp = datetime.utcnow().isoformat() + "Z"
    payload = {"ts": timestamp, "event": event, **fields}
    try:
        logging.log(level, json.dumps(payload, ensure_ascii=False, default=_json_default))
    except TypeError:
        safe_payload = {k: str(v) for k, v in payload.items()}
        logging.log(level, json.dumps(safe_payload, ensure_ascii=False))
    entry = {"severity": logging.getLevelName(level), **payload}
    if EVENT_JOURNAL:
        EVENT_JOURNAL.append(entry)
    else:
        EARLY_EVENTS.append(entry)


def log_exception(event: str, exc: BaseException, **fields: Any) -> None:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_event(event, level=logging.ERROR, error=str(exc), traceback=tb, **fields)


def build_search_url(cfg: Dict[str, Any]) -> str:
    sc = cfg.get("search_config") or {}
    keyword = sc.get("keyword", "chatgpt")
    hashtag = sc.get("hashtag", "zonauang")
    src = sc.get("src", "recent_search_click")
    live = sc.get("live", True)

    query = quote(f"{keyword} #{hashtag}")
    url = f"https://x.com/search?q={query}&src={quote(src)}"
    if live:
        url += "&f=live"
    return url


def load_replied() -> List[int]:
    data = load_json(REPLIED_LOG)
    replied = data if isinstance(data, list) else []
    log_event("replied_loaded", count=len(replied))
    return replied


def save_replied(ids: List[int]) -> None:
    tmp = REPLIED_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, REPLIED_LOG)
    log_event("replied_saved", count=len(ids))


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower().strip()


def passes_prefilter(text: str, pos_keywords: List[str], neg_keywords: List[str]) -> bool:
    """Return True if normalized *text* matches positive keywords and none of the negative ones."""
    if not text or len(text) < 5:
        return False
    if not any(k in text for k in pos_keywords):
        return False
    if any(n in text for n in neg_keywords):
        return False
    return True


async def wait_for_manual_captcha() -> None:
    console.print("[bold yellow]⚠️ CAPTCHA terdeteksi. Selesaikan di browser dan tekan Enter…[/]")
    await asyncio.to_thread(input)
    log_event("captcha_resolved")


async def detect_captcha(page, stats: Optional[Dict[str, int]] = None) -> None:
    u = page.url.lower()
    if "captcha" in u or "challenge" in u:
        if stats is not None:
            stats["captcha"] = stats.get("captcha", 0) + 1
        log_event("captcha_detected", url=page.url)
        await wait_for_manual_captcha()
        return
    try:
        if await page.query_selector("iframe[src*='captcha']"):
            if stats is not None:
                stats["captcha"] = stats.get("captcha", 0) + 1
            log_event("captcha_detected", url=page.url)
            await wait_for_manual_captcha()
    except TimeoutError:
        pass


async def detect_rate_limit(page, stats: Optional[Dict[str, int]] = None) -> bool:
    try:
        if await page.query_selector("text='Rate limit exceeded'"):
            if stats is not None:
                stats["rate"] = stats.get("rate", 0) + 1
            log_event("rate_limit", url=page.url)
            return True
    except TimeoutError:
        pass
    return False


# ------------------- Data kelas -------------------


@dataclass
class Candidate:
    tid: int
    author: str
    created_at: datetime
    element: Any
    text: str


@dataclass
class ReplyResult:
    action: str  # "reply" atau "skip"
    reason: str
    durations: Dict[str, int] = field(default_factory=dict)


# ----------------- Login & navigasi -----------------


async def wait_until_logged_in(page, max_ms: int) -> bool:
    """Menunggu hingga ikon profil muncul yang menandakan login sukses."""
    try:
        await page.wait_for_selector("[data-testid='AppTabBar_Profile_Link']", timeout=max_ms)
        await page.context.storage_state(path=COOKIE_FILE)
        return True
    except TimeoutError:
        return False


async def ensure_logged_in(page, search_url: str, net_cfg: Dict[str, int], stats: Dict[str, int]) -> bool:
    """Pastikan sesi login aktif dan halaman hasil pencarian siap."""
    try:
        await page.wait_for_selector("[data-testid='AppTabBar_Profile_Link']", timeout=3000)
        return True
    except TimeoutError:
        pass

    await resilient_goto(page, LOGIN_URL, net_cfg, stats)
    if await wait_until_logged_in(page, 120000):
        await resilient_goto(page, search_url, net_cfg, stats)
        return True
    return False


async def resilient_goto(page, url: str, net_cfg: Dict[str, int], stats: Dict[str, int]) -> bool:
    """Pergi ke URL dengan retry dan backoff."""
    for attempt in range(net_cfg["max_retries"]):
        try:
            log_event("goto_attempt", level=logging.DEBUG, url=url, attempt=attempt + 1)
            await page.goto(url, timeout=net_cfg["timeout_ms"], wait_until="domcontentloaded")
            await detect_captcha(page, stats)
            log_event("goto_success", level=logging.DEBUG, url=url, attempt=attempt + 1)
            return True
        except Exception as exc:
            logging.warning(
                "Gagal membuka %s (percobaan %d/%d): %s",
                url,
                attempt + 1,
                net_cfg["max_retries"],
                exc,
            )
            log_event("goto_retry", level=logging.WARNING, url=url, attempt=attempt + 1, error=str(exc))
            await asyncio.sleep(net_cfg["retry_backoff_ms"] / 1000 * (attempt + 1))
    logging.error("Gagal membuka %s setelah %d percobaan", url, net_cfg["max_retries"])
    log_event("goto_failed", url=url, attempts=net_cfg["max_retries"])
    return False


# ----------------- Pemindaian & prioritas -----------------


async def soft_scan_cycle(
    page,
    scan_cfg: Dict[str, int],
    replied: set[int],
    seen_ids: set[int],
    stats: Dict[str, int],
) -> List[Candidate]:
    """Scan DOM untuk tweet baru tanpa reload."""
    candidates: List[Candidate] = []
    log_event("scan_cycle_start", level=logging.DEBUG)
    arts = await page.query_selector_all("article")
    stats["found"] = stats.get("found", 0) + len(arts)
    stats["found_last"] = len(arts)

    for art in arts:
        link = await art.query_selector("a[href*='/status/']")
        if not link:
            continue
        href = await link.get_attribute("href")
        if not href:
            continue
        try:
            tid = int(href.split("/")[-1])
        except ValueError:
            continue
        user = href.split("/")[1]
        if tid in seen_ids or tid in replied:
            continue
        tm = await art.query_selector("time")
        if not tm:
            continue
        ts = await tm.get_attribute("datetime")
        if not ts:
            continue
        created = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created
        if age > timedelta(hours=scan_cfg["max_age_hours"]):
            stats["age"] = stats.get("age", 0) + 1
            seen_ids.add(tid)
            continue
        text = await art.inner_text()
        candidates.append(Candidate(tid, user, created, art, text))
        seen_ids.add(tid)

    # scroll ringan untuk memunculkan item baru
    await page.mouse.wheel(0, 1000)
    log_event(
        "scan_cycle_end",
        level=logging.DEBUG,
        total=len(arts),
        fresh=len(candidates),
        ignored_old=stats.get("age", 0),
    )
    return candidates


def prioritize(candidates: List[Candidate]) -> List[Candidate]:
    return sorted(candidates, key=lambda c: c.created_at, reverse=True)


# ------------------- Balasan & log --------------------


async def attempt_reply(
    page,
    cand: Candidate,
    reply_msg: str,
    reply_cfg: Dict[str, int],
    state: Dict[str, Any],
    replied: set[int],
    stats: Dict[str, int],
    timers: Dict[str, List[int]],
) -> ReplyResult:
    if cand.tid in replied:
        stats["duplicate"] = stats.get("duplicate", 0) + 1
        return ReplyResult("skip", "duplicate")

    btn = await cand.element.query_selector("[data-testid='reply']")
    if not btn or await btn.get_attribute("aria-disabled") == "true":
        stats["skip_tombol"] = stats.get("skip_tombol", 0) + 1
        return ReplyResult("skip", "skip_tombol")

    log_event(
        "reply_attempt",
        level=logging.DEBUG,
        tweet_id=cand.tid,
        author=cand.author,
        dry_run=state.get("dry_run", False),
    )
    t0 = time.perf_counter()
    try:
        await btn.click(timeout=reply_cfg["click_timeout_ms"])
        t1 = time.perf_counter()
        await page.wait_for_selector("div[role='textbox']", timeout=reply_cfg["composer_timeout_ms"])
        t2 = time.perf_counter()
        await page.fill("div[role='textbox']", reply_msg)
        if state["dry_run"]:
            await page.keyboard.press("Escape")
            result = ReplyResult(
                "skip",
                "dry_run",
                {
                    "candidate_to_click": int((t1 - t0) * 1000),
                    "click_to_textbox": int((t2 - t1) * 1000),
                },
            )
            log_event("reply_dry_run", tweet_id=cand.tid, author=cand.author, durations=result.durations)
            return result
        send_btn = await page.query_selector("[data-testid='tweetButton']")
        if not send_btn or await send_btn.get_attribute("aria-disabled") == "true":
            stats["skip_closed"] = stats.get("skip_closed", 0) + 1
            await page.keyboard.press("Escape")
            log_event("reply_closed", tweet_id=cand.tid, author=cand.author)
            return ReplyResult("skip", "reply_closed")
        await send_btn.click(timeout=reply_cfg["submit_timeout_ms"])
        t3 = time.perf_counter()
    except TimeoutError:
        stats["net_error"] = stats.get("net_error", 0) + 1
        log_event("reply_timeout", level=logging.WARNING, tweet_id=cand.tid, author=cand.author)
        return ReplyResult("skip", "net_error")

    durations = {
        "candidate_to_click": int((t1 - t0) * 1000),
        "click_to_textbox": int((t2 - t1) * 1000),
        "textbox_to_submit": int((t3 - t2) * 1000),
    }

    try:
        toast = await page.wait_for_selector("[data-testid='toast']", timeout=2000)
        msg = (await toast.inner_text()).lower() if toast else ""
        if "reply" in msg and ("can't" in msg or "cannot" in msg or "tidak" in msg):
            stats["skip_closed"] = stats.get("skip_closed", 0) + 1
            await page.keyboard.press("Escape")
            log_event("reply_closed_toast", tweet_id=cand.tid, author=cand.author, message=msg)
            return ReplyResult("skip", "reply_closed", durations)
    except TimeoutError:
        pass

    for k, v in durations.items():
        timers.setdefault(k, []).append(v)
        if len(timers[k]) > 10:
            timers[k] = timers[k][-10:]

    replied.add(cand.tid)
    save_replied(list(replied))
    stats["replied"] = stats.get("replied", 0) + 1
    log_event("reply_sent", tweet_id=cand.tid, author=cand.author, durations=durations)
    return ReplyResult("reply", "balas_ok", durations)


def record_decision(
    cand: Candidate,
    res: ReplyResult,
    path: str = "decisions.log",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    text_excerpt = " ".join(cand.text.split())[:200]
    data: Dict[str, Any] = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "tweet_id": cand.tid,
        "author": cand.author,
        "tweet_url": f"https://x.com/{cand.author}/status/{cand.tid}",
        "age_min": int((datetime.now(timezone.utc) - cand.created_at).total_seconds() / 60),
        "action": res.action,
        "reason": res.reason,
        "dur_ms": res.durations,
        "excerpt": text_excerpt,
    }
    if extra:
        data["meta"] = extra
    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
    except Exception as exc:
        logging.warning("Gagal menulis log keputusan: %s", exc)
        log_event("decision_log_failed", level=logging.WARNING, error=str(exc))
    log_event("decision", **data)


def record_cycle(
    cycle: int,
    found: int,
    new_candidates: int,
    dur_ms: int,
    refreshed: bool,
    path: str = "cycles.log",
    stats_snapshot: Optional[Dict[str, int]] = None,
) -> None:
    data: Dict[str, Any] = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "cycle": cycle,
        "found": found,
        "new_candidates": new_candidates,
        "scan_cycle_ms": dur_ms,
        "refreshed": refreshed,
    }
    if stats_snapshot:
        data["stats"] = stats_snapshot
    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
    except Exception as exc:
        logging.warning("Gagal menulis log siklus: %s", exc)
        log_event("cycle_log_failed", level=logging.WARNING, error=str(exc))
    log_event("cycle", **data)


# ---------------- Dashboard & input -----------------


def render_dashboard(
    stats: Dict[str, int],
    timers: Dict[str, List[int]],
    status: Dict[str, Any],
    activity: List[str],
) -> Panel:
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    headers = [
        "Ditemukan",
        "Calon Balas",
        "Sudah Balas",
        "Skip Kata",
        "Skip Tombol",
    ]
    for h in headers:
        table.add_column(h, justify="center")
    row = [
        str(stats.get("found", 0)),
        str(stats.get("cand", 0)),
        str(stats.get("replied", 0)),
        str(stats.get("skip_kata", 0)),
        str(stats.get("skip_tombol", 0)),
    ]
    table.add_row(*row)

    lines = [f"Terlalu Lama: {stats.get('age', 0)}"]
    if status.get("ai_enabled"):
        lines.append(f"Ambigu AI: {stats.get('ai_amb', 0)}")
        lines.append(f"AI Fallback: {stats.get('ai_disabled', 0)}")
    last = status.get("last_activity")
    if last:
        user, ts = last
        lines.append(f"Aktivitas Terakhir: @{user} • {ts.strftime('%H:%M:%S')}")
    else:
        lines.append("Aktivitas Terakhir: -")
    if status.get("url"):
        login = "OK" if status.get("logged_in") else "LOGIN"
        cdur = status.get("cycle_dur", 0) / 1000
        lines.append(f"URL Aktif: {status['url']} • {login} • {cdur:.1f}s")

    summary = Text("\n".join(lines))

    log_lines = activity[-5:]
    log_text = Text("\n".join(log_lines) if log_lines else "-")
    log_panel = Panel(log_text, title="Log Aktivitas", padding=(0, 1))

    grp = Group(status.get("spinner", Text("")), table, summary, log_panel)
    return Panel(grp, padding=0)


async def key_listener(state: Dict[str, Any]) -> None:
    """Listener non-blocking untuk input keyboard."""
    while True:
        ch = await asyncio.to_thread(sys.stdin.read, 1)
        ch = ch.strip().lower()
        if ch == "p":
            state["paused"] = not state["paused"]
            log_event("key_toggle_pause", paused=state["paused"])
        elif ch == "r":
            state["force_refresh"] = True
            log_event("key_force_refresh")
        elif ch == "d":
            state["dry_run"] = not state["dry_run"]
            log_event("key_toggle_dry_run", dry_run=state["dry_run"])
        elif ch == "q":
            state["quit"] = True
            log_event("key_quit")


# ----------------------------- Main loop -----------------------------


async def run() -> None:
    log_event("bot_start")
    load_env()
    cfg = load_config()
    setup_logging(cfg["logging"])
    global SEARCH_URL
    SEARCH_URL = build_search_url(cfg)
    pos_kws = [normalize_text(k) for k in cfg.get("positive_keywords", [])]
    neg_kws = [normalize_text(k) for k in cfg.get("negative_keywords", [])]
    reply_msg = cfg.get("reply_message", "")

    ai_enabled = cfg.get("ai_enabled", False)
    ai_timeout = cfg.get("ai_timeout_ms", 4000)
    pre_filter = cfg.get("pre_filter_keywords", True)
    if ai_enabled and not ensure_api_key():
        log_event("bot_stop_missing_api_key")
        return

    scan_cfg = cfg["scan"]
    net_cfg = cfg["network"]
    reply_cfg = cfg["reply"]

    replied = set(load_replied())
    seen_ids: set[int] = set()
    stats: Dict[str, int] = {}
    timers: Dict[str, List[int]] = {"scan_cycle": []}
    state = {"paused": False, "force_refresh": False, "dry_run": reply_cfg.get("dry_run", False), "quit": False}
    last_activity: Optional[tuple[str, datetime]] = None
    activity: List[str] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR, headless=False, args=["--start-maximized"]
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    # buka halaman login dan tunggu sampai benar-benar masuk
    await resilient_goto(page, LOGIN_URL, net_cfg, stats)
    login_spinner = Spinner("dots", text="Menunggu login…")
    with Live(login_spinner, console=console, refresh_per_second=10):
        logged = await wait_until_logged_in(page, 120000)
    if not logged:
        console.print("[bold red]Gagal login.[/]")
        log_event("login_failed")
        await browser.close()
        await pw.stop()
        return
    log_event("login_success")

    await resilient_goto(page, SEARCH_URL, net_cfg, stats)
    work_spinner = Spinner("line")

    if cfg["dashboard"].get("interactive_keys", True):
        asyncio.create_task(key_listener(state))

    cycle = 0
    no_new = 0

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        while not state["quit"]:
            start = time.perf_counter()
            logged_in = await ensure_logged_in(page, SEARCH_URL, net_cfg, stats)
            if not logged_in:
                log_event("ensure_login_failed", level=logging.WARNING)
                await asyncio.sleep(1)
                continue
            try:
                await page.wait_for_selector("article", timeout=net_cfg["timeout_ms"])
            except TimeoutError:
                await resilient_goto(page, SEARCH_URL, net_cfg, stats)
                continue
            refreshed = False

            new_candidates: List[Candidate] = []
            if not state["paused"]:
                try:
                    cands = await soft_scan_cycle(page, scan_cfg, replied, seen_ids, stats)
                    stats["cand"] = stats.get("cand", 0) + len(cands)
                    for cand in prioritize(cands):
                        norm_text = normalize_text(cand.text)
                        if pre_filter and not passes_prefilter(norm_text, pos_kws, neg_kws):
                            stats["skip_kata"] = stats.get("skip_kata", 0) + 1
                            record_decision(
                                cand,
                                ReplyResult("skip", "prefilter"),
                                extra={"prefilter": True},
                            )
                            activity.append(f"@{cand.author} skip: kata")
                            if len(activity) > 10:
                                activity.pop(0)
                            continue
                        label = "pembeli"
                        if ai_enabled:
                            cache_key = norm_text
                            now_ms = int(time.time() * 1000)
                            cached = AI_CACHE.get(cache_key)
                            if cached and now_ms - cached[1] < AI_CACHE_TTL_MS:
                                label = cached[0]
                                log_event(
                                    "ai_cache_hit",
                                    level=logging.DEBUG,
                                    tweet_id=cand.tid,
                                    author=cand.author,
                                    label=label,
                                )
                            else:
                                ai_start = time.perf_counter()
                                res = await classify_text(cand.text, timeout_ms=ai_timeout)
                                elapsed_ms = int((time.perf_counter() - ai_start) * 1000)
                                if res is None:
                                    stats["ai_disabled"] = stats.get("ai_disabled", 0) + 1
                                    logging.warning(
                                        "AI classification unavailable; proceeding without filter"
                                    )
                                    log_event(
                                        "ai_unavailable",
                                        tweet_id=cand.tid,
                                        author=cand.author,
                                        latency_ms=elapsed_ms,
                                    )
                                else:
                                    label = res
                                    AI_CACHE[cache_key] = (label, now_ms)
                                    log_event(
                                        "ai_classify",
                                        tweet_id=cand.tid,
                                        author=cand.author,
                                        label=label,
                                        latency_ms=elapsed_ms,
                                    )
                            if label != "pembeli":
                                stats["ai_amb"] = stats.get("ai_amb", 0) + 1
                                record_decision(
                                    cand,
                                    ReplyResult("skip", "ai_amb"),
                                    extra={"ai_label": label},
                                )
                                activity.append(f"@{cand.author} skip: ai_amb")
                                if len(activity) > 10:
                                    activity.pop(0)
                                continue

                        res = await attempt_reply(page, cand, reply_msg, reply_cfg, state, replied, stats, timers)
                        record_decision(cand, res)
                        activity.append(f"@{cand.author} {res.action}: {res.reason}")
                        if len(activity) > 10:
                            activity.pop(0)
                        if res.action == "reply":
                            last_activity = (cand.author, datetime.now())
                    new_candidates = cands
                except Exception as exc:
                    logging.exception("Kesalahan saat memproses kandidat; lanjut ke siklus berikutnya")
                    log_exception("candidate_loop_failed", exc)

            dur = int((time.perf_counter() - start) * 1000)
            timers["scan_cycle"].append(dur)
            if len(timers["scan_cycle"]) > 10:
                timers["scan_cycle"] = timers["scan_cycle"][-10:]

            if not new_candidates:
                no_new += 1
                if state["force_refresh"] or no_new >= scan_cfg["no_new_cycles_before_refresh"]:
                    await page.reload()
                    refreshed = True
                    state["force_refresh"] = False
                    no_new = 0
            else:
                no_new = 0

            status = {
                "last_activity": last_activity,
                "url": SEARCH_URL,
                "logged_in": logged_in,
                "ai_enabled": ai_enabled,
                "cycle_dur": dur,
                "spinner": work_spinner,
            }
            live.update(render_dashboard(stats, timers, status, activity))

            cycle += 1
            record_cycle(
                cycle,
                stats.get("found_last", 0),
                len(new_candidates),
                dur,
                refreshed,
                stats_snapshot=dict(stats),
            )

            await asyncio.sleep(scan_cfg["scan_interval_ms"] / 1000)

    await browser.close()
    await pw.stop()
    log_event("bot_stop")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("[red]🛑 Bot dihentikan oleh user.[/]")
        log_event("bot_stop_keyboard")
    except Exception as e:
        log_exception("startup_error", e)
        console.print(f"[bold red]Error startup:[/] {e}")
        sys.exit(1)

