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
import sys
import tempfile
import time
import unicodedata
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

LOGIN_SUCCESS_SELECTORS = [
    "[data-testid='AppTabBar_Profile_Link']",
    "[data-testid='AppTabBar_Home_Link']",
    "[data-testid='AppTabBar_Notifications_Link']",
    "[data-testid='AppTabBar_DirectMessage_Link']",
    "[data-testid='SideNav_AccountSwitcher_Button']",
]

LOGIN_URL_HINTS = ("/login", "/i/flow/login")

# ---------------- Konfigurasi & util dasar -----------------

CONFIG_PATH = "bot_config.json"
REPLIED_LOG = "replied_ids.json"
SESSION_DIR = "bot_session"

DEFAULT_SCAN = {
    "scan_interval_ms": 1500,
    "no_new_cycles_before_refresh": 6,
    "max_age_hours": 3,
}

DEFAULT_NETWORK = {
    "timeout_ms": 15000,
    "max_retries": 3,
    "retry_backoff_ms": 1200,
    "health_max_retries": 3,
    "stuck_wait_ms": 2000,
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

DEFAULT_SESSION = {
    "user_data_dir": SESSION_DIR,
    "chrome_profile_dir": "",
    "browser_channel": "chrome",
    "cookies_path": "",
}

# Default search URL akan dibangun ulang dari config
SEARCH_URL = "https://x.com/search?q=chatgpt%20%23zonauang&src=recent_search_click&f=live"

AI_CACHE_TTL_MS = 86_400_000  # 24 jam
AI_CACHE: Dict[str, Tuple[str, int]] = {}

ENV_FILE = ".env"


def load_env() -> None:
    """Load key-value pairs from ENV_FILE into environment variables."""
    if not os.path.exists(ENV_FILE):
        return
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    os.environ.setdefault(key, val)
    except OSError:
        pass


def ensure_api_key() -> bool:
    """Pastikan OPENAI_API_KEY tersedia; jika tidak, minta user dan simpan."""
    if os.getenv("OPENAI_API_KEY"):
        return True
    try:
        from getpass import getpass

        key = getpass("Masukkan OPENAI_API_KEY: ").strip()
    except Exception:
        key = input("Masukkan OPENAI_API_KEY: ").strip()
    if not key:
        console.print("[bold red]OPENAI_API_KEY diperlukan untuk AI.[/]")
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
    return True




def load_json(path: str) -> Optional[Any]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
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
    cfg["session"] = {**DEFAULT_SESSION, **cfg.get("session", {})}
    return cfg


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
    return data if isinstance(data, list) else []


def save_replied(ids: List[int]) -> None:
    tmp = REPLIED_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, REPLIED_LOG)


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


async def detect_captcha(page, stats: Optional[Dict[str, int]] = None) -> None:
    u = page.url.lower()
    if "captcha" in u or "challenge" in u:
        if stats is not None:
            stats["captcha"] = stats.get("captcha", 0) + 1
        await wait_for_manual_captcha()
        return
    try:
        if await page.query_selector("iframe[src*='captcha']"):
            if stats is not None:
                stats["captcha"] = stats.get("captcha", 0) + 1
            await wait_for_manual_captcha()
    except TimeoutError:
        pass


async def detect_rate_limit(page, stats: Optional[Dict[str, int]] = None) -> bool:
    try:
        if await page.query_selector("text='Rate limit exceeded'"):
            if stats is not None:
                stats["rate"] = stats.get("rate", 0) + 1
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


async def _get_pathname(page) -> str:
    try:
        return await page.evaluate("() => window.location ? window.location.pathname : ''")
    except Exception:
        return ""


async def _on_login_page(page) -> bool:
    url = page.url or ""
    if any(hint in url for hint in LOGIN_URL_HINTS):
        return True
    path = await _get_pathname(page)
    return bool(path and any(path.startswith(hint) for hint in LOGIN_URL_HINTS))


async def _has_login_indicator(page) -> tuple[bool, str]:
    """Return (True, reason) jika indikator login ditemukan dan bukan halaman login."""
    if await _on_login_page(page):
        return False, "still_on_login_page"
    for selector in LOGIN_SUCCESS_SELECTORS:
        try:
            handle = await page.query_selector(selector)
        except Exception:
            handle = None
        if handle:
            return True, selector
    return False, "selectors_missing"


async def apply_session_cookies(context, cookies_path: str) -> bool:
    """Tambahkan cookies eksternal (hasil export Chrome) ke konteks browser."""
    if not cookies_path:
        return False
    expanded = os.path.expanduser(cookies_path)
    if not os.path.exists(expanded):
        console.log(f"[session] File cookies tidak ditemukan: {expanded}")
        return False
    data = load_json(expanded)
    cookies: List[Dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        cookies = data["cookies"]
    elif isinstance(data, list):
        cookies = data
    else:
        console.log(f"[session] Format cookies tidak dikenali: {expanded}")
        return False
    valid = [c for c in cookies if isinstance(c, dict) and c.get("name") and c.get("value")]
    if not valid:
        console.log(f"[session] Tidak ada cookie valid di {expanded}")
        return False
    try:
        await context.add_cookies(valid)
    except Exception as exc:
        console.log(f"[session] Gagal menerapkan cookies eksternal: {exc}")
        return False
    console.log(f"[session] {len(valid)} cookies eksternal diterapkan dari {expanded}.")
    return True


async def resilient_goto(page, url: str, net_cfg: Dict[str, int], stats: Dict[str, int]) -> bool:
    """Pergi ke URL dengan retry dan backoff."""
    for attempt in range(net_cfg["max_retries"]):
        try:
            await page.goto(url, timeout=net_cfg["timeout_ms"], wait_until="domcontentloaded")
            await detect_captcha(page, stats)
            return True
        except Exception as exc:
            stats["goto_retry"] = stats.get("goto_retry", 0) + 1
            console.log(f"[net] goto gagal (attempt {attempt + 1}/{net_cfg['max_retries']}): {exc}")
            await asyncio.sleep(net_cfg["retry_backoff_ms"] / 1000 * (attempt + 1))
    return False


async def _safe_query(page, selector: str) -> bool:
    try:
        return bool(await page.query_selector(selector))
    except Exception:
        return False


async def _page_ready_state(page) -> str:
    try:
        return await page.evaluate("() => document.readyState || ''")
    except Exception:
        return ""


async def _timeline_has_error(page) -> bool:
    error_selectors = [
        "text='Something went wrong'",
        "text='Try again'",
        "text='Reload'",
        "[data-testid='error-detail']",
    ]
    for sel in error_selectors:
        if await _safe_query(page, sel):
            return True
    return False


async def timeline_looks_stuck(page) -> bool:
    """Heuristik sederhana untuk mendeteksi timeline yang macet/half-loaded."""
    ready_state = await _page_ready_state(page)
    spinner = await _safe_query(page, "div[role='progressbar']")
    has_error = await _timeline_has_error(page)
    has_article = await _safe_query(page, "article")
    if has_error:
        return True
    if spinner and ready_state != "complete":
        return True
    if not has_article:
        if ready_state != "complete":
            return True
        empty_ok = await _safe_query(page, "text='No results'") or await _safe_query(page, "text='No results for'")
        return not empty_ok
    return False


async def recover_timeline(page, search_url: str, net_cfg: Dict[str, int], stats: Dict[str, int], reason: str) -> None:
    stats["page_recoveries"] = stats.get("page_recoveries", 0) + 1
    console.log(f"[health] Memicu pemulihan timeline ({reason}).")
    try:
        await page.reload(timeout=net_cfg["timeout_ms"], wait_until="domcontentloaded")
        return
    except Exception as exc:
        stats["reload_fail"] = stats.get("reload_fail", 0) + 1
        console.log(f"[health] Reload gagal: {exc}. Memaksa goto ulang.")
    await resilient_goto(page, search_url, net_cfg, stats)


async def ensure_timeline_ready(page, search_url: str, net_cfg: Dict[str, int], stats: Dict[str, int]) -> bool:
    """Pastikan artikel timeline termuat dan tidak macet sebelum memproses siklus."""
    max_attempts = net_cfg.get("health_max_retries", 1)
    for attempt in range(max_attempts):
        try:
            await page.wait_for_selector("article", timeout=net_cfg["timeout_ms"])
        except TimeoutError:
            stats["timeline_timeout"] = stats.get("timeline_timeout", 0) + 1
            await recover_timeline(page, search_url, net_cfg, stats, reason="wait_timeout")
            await asyncio.sleep(net_cfg["stuck_wait_ms"] / 1000)
            continue

        if not await timeline_looks_stuck(page):
            return True

        stats["timeline_stuck"] = stats.get("timeline_stuck", 0) + 1
        await recover_timeline(page, search_url, net_cfg, stats, reason="half_loaded")
        await asyncio.sleep(net_cfg["stuck_wait_ms"] / 1000)
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

    t0 = time.perf_counter()
    try:
        await btn.click(timeout=reply_cfg["click_timeout_ms"])
        t1 = time.perf_counter()
        await page.wait_for_selector("div[role='textbox']", timeout=reply_cfg["composer_timeout_ms"])
        t2 = time.perf_counter()
        await page.fill("div[role='textbox']", reply_msg)
        if state["dry_run"]:
            await page.keyboard.press("Escape")
            return ReplyResult("skip", "dry_run", {
                "candidate_to_click": int((t1 - t0) * 1000),
                "click_to_textbox": int((t2 - t1) * 1000),
            })
        send_btn = await page.query_selector("[data-testid='tweetButton']")
        if not send_btn or await send_btn.get_attribute("aria-disabled") == "true":
            stats["skip_closed"] = stats.get("skip_closed", 0) + 1
            await page.keyboard.press("Escape")
            return ReplyResult("skip", "reply_closed")
        await send_btn.click(timeout=reply_cfg["submit_timeout_ms"])
        t3 = time.perf_counter()
    except TimeoutError:
        stats["net_error"] = stats.get("net_error", 0) + 1
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
    return ReplyResult("reply", "balas_ok", durations)


def record_decision(cand: Candidate, res: ReplyResult, path: str = "decisions.log") -> None:
    data = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "tweet_id": cand.tid,
        "author": cand.author,
        "age_min": int((datetime.now(timezone.utc) - cand.created_at).total_seconds() / 60),
        "action": res.action,
        "reason": res.reason,
        "dur_ms": res.durations,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass


def record_cycle(
    cycle: int, found: int, new_candidates: int, dur_ms: int, refreshed: bool, path: str = "cycles.log"
) -> None:
    data = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "cycle": cycle,
        "found": found,
        "new_candidates": new_candidates,
        "scan_cycle_ms": dur_ms,
        "refreshed": refreshed,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass


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
        elif ch == "r":
            state["force_refresh"] = True
        elif ch == "d":
            state["dry_run"] = not state["dry_run"]
        elif ch == "q":
            state["quit"] = True


# ----------------------------- Main loop -----------------------------


async def run() -> None:
    load_env()
    cfg = load_config()
    global SEARCH_URL
    SEARCH_URL = build_search_url(cfg)
    pos_kws = [normalize_text(k) for k in cfg.get("positive_keywords", [])]
    neg_kws = [normalize_text(k) for k in cfg.get("negative_keywords", [])]
    reply_msg = cfg.get("reply_message", "")

    ai_enabled = cfg.get("ai_enabled", False)
    ai_timeout = cfg.get("ai_timeout_ms", 4000)
    pre_filter = cfg.get("pre_filter_keywords", True)
    if ai_enabled and not ensure_api_key():
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

    session_cfg = cfg["session"].copy()
    session_cfg["chrome_profile_dir"] = session_cfg.get("chrome_profile_dir") or os.getenv("CHROME_PROFILE_DIR", "")
    session_cfg["cookies_path"] = session_cfg.get("cookies_path") or os.getenv("TWITTER_COOKIES_PATH", "")
    session_path = session_cfg.get("chrome_profile_dir") or session_cfg.get("user_data_dir") or SESSION_DIR
    session_path = os.path.expanduser(session_path)
    if session_cfg.get("chrome_profile_dir") and not os.path.exists(session_path):
        console.print(
            f"[bold red]Profil Chrome tidak ditemukan:[/] {session_path}\nSetel `session.chrome_profile_dir` ke path profil yang benar atau kosongkan untuk memakai direktori lokal."
        )
        return

    pw = await async_playwright().start()
    launch_kwargs: Dict[str, Any] = {
        "user_data_dir": session_path,
        "headless": False,
        "args": ["--start-maximized"],
    }
    channel = session_cfg.get("browser_channel") or None
    if channel:
        launch_kwargs["channel"] = channel
    console.log(f"[session] Memakai profil browser: {session_path} (channel={channel or 'default'})")
    browser = await pw.chromium.launch_persistent_context(**launch_kwargs)
    page = browser.pages[0] if browser.pages else await browser.new_page()

    await apply_session_cookies(browser, session_cfg.get("cookies_path", ""))

    await resilient_goto(page, SEARCH_URL, net_cfg, stats)
    logged, reason = await _has_login_indicator(page)
    if not logged:
        console.print(
            "[bold red]Sesi/cookies X tidak valid. Perbarui profil Chrome atau export cookies terbaru lalu jalankan ulang bot.[/]"
        )
        console.log(f"[session] Login indicator missing: {reason}")
        await browser.close()
        await pw.stop()
        return
    if not await ensure_timeline_ready(page, SEARCH_URL, net_cfg, stats):
        console.print("[bold red]Halaman pencarian tidak siap setelah beberapa percobaan.[/]")
        await browser.close()
        await pw.stop()
        return
    work_spinner = Spinner("line")

    if cfg["dashboard"].get("interactive_keys", True):
        asyncio.create_task(key_listener(state))

    cycle = 0
    no_new = 0

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        while not state["quit"]:
            start = time.perf_counter()
            logged_in, login_reason = await _has_login_indicator(page)
            if not logged_in:
                console.print(
                    "[bold red]Sesi X tidak lagi valid di tengah jalan. Segarkan cookies/profil dan jalankan ulang bot.[/]"
                )
                console.log(f"[session] Login indicator hilang: {login_reason}")
                break
            if not await ensure_timeline_ready(page, SEARCH_URL, net_cfg, stats):
                await asyncio.sleep(1)
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
                            record_decision(cand, ReplyResult("skip", "prefilter"))
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
                            else:
                                res = await classify_text(cand.text, timeout_ms=ai_timeout)
                                if res is None:
                                    stats["ai_disabled"] = stats.get("ai_disabled", 0) + 1
                                    logging.warning(
                                        "AI classification unavailable; proceeding without filter"
                                    )
                                else:
                                    label = res
                                    AI_CACHE[cache_key] = (label, now_ms)
                            if label != "pembeli":
                                stats["ai_amb"] = stats.get("ai_amb", 0) + 1
                                record_decision(cand, ReplyResult("skip", "ai_amb"))
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
                except Exception:
                    # loop resilien: lanjut saja
                    pass

            dur = int((time.perf_counter() - start) * 1000)
            timers["scan_cycle"].append(dur)
            if len(timers["scan_cycle"]) > 10:
                timers["scan_cycle"] = timers["scan_cycle"][-10:]

            if not new_candidates:
                no_new += 1
                if state["force_refresh"] or no_new >= scan_cfg["no_new_cycles_before_refresh"]:
                    reason = "force_refresh" if state["force_refresh"] else "stale_timeline"
                    await recover_timeline(page, SEARCH_URL, net_cfg, stats, reason=reason)
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
            record_cycle(cycle, stats.get("found_last", 0), len(new_candidates), dur, refreshed)

            await asyncio.sleep(scan_cfg["scan_interval_ms"] / 1000)

    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("[red]🛑 Bot dihentikan oleh user.[/]")
    except Exception as e:
        console.print(f"[bold red]Error startup:[/] {e}")
        sys.exit(1)

