#!/usr/bin/env python3
"""Bot balas X/Twitter dengan Playwright.

Fitur utama:
* Soft-scan: memindai DOM tanpa reload setiap siklus.
* Smart refresh: reload hanya saat stagnan atau dipaksa.
* Health-check sesi otomatis & login resilient.
* Prioritas kandidat berdasarkan waktu.
* Dashboard Rich 5 kolom dengan kontrol interaktif.
* Anti duplikasi balasan menggunakan log id atomik.
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
from typing import Any, Dict, List, Optional

from urllib.parse import quote

from playwright.async_api import TimeoutError, async_playwright

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner

from ai import ZeroShotClient

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

# Default search URL akan dibangun ulang dari config
SEARCH_URL = "https://x.com/search?q=chatgpt%20%23zonauang&src=recent_search_click&f=live"


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


def load_hf_token(path: str = "tokens.json") -> Optional[str]:
    """Load Hugging Face API token from JSON file."""
    data = load_json(path)
    if isinstance(data, dict):
        token = data.get("hf_api_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def save_hf_token(token: str, path: str = "tokens.json") -> None:
    """Persist Hugging Face token atomically to disk."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"hf_api_token": token}, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def ensure_hf_token() -> str:
    """Ensure HF_API_TOKEN exists by prompting user if needed."""
    token = os.environ.get("HF_API_TOKEN") or load_hf_token()
    while not token:
        token = input("Masukkan Hugging Face API token: ").strip()
    save_hf_token(token)
    os.environ["HF_API_TOKEN"] = token
    return token


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
    if not text:
        return False
    t = normalize_text(text)
    if len(t) < 5:
        return False
    if not any(k.lower() in t for k in pos_keywords):
        return False
    if any(n.lower() in t for n in neg_keywords):
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

    await resilient_goto(page, "https://x.com/login", net_cfg, stats)
    if await wait_until_logged_in(page, 120000):
        await resilient_goto(page, search_url, net_cfg, stats)
        return True
    return False


async def resilient_goto(page, url: str, net_cfg: Dict[str, int], stats: Dict[str, int]) -> bool:
    """Pergi ke URL dengan retry dan backoff."""
    for attempt in range(net_cfg["max_retries"]):
        try:
            await page.goto(url, timeout=net_cfg["timeout_ms"], wait_until="domcontentloaded")
            await detect_captcha(page, stats)
            return True
        except Exception:
            await asyncio.sleep(net_cfg["retry_backoff_ms"] / 1000 * (attempt + 1))
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
        await page.click("[data-testid='tweetButton']", timeout=reply_cfg["submit_timeout_ms"])
        t3 = time.perf_counter()
    except TimeoutError:
        stats["net_error"] = stats.get("net_error", 0) + 1
        return ReplyResult("skip", "net_error")

    durations = {
        "candidate_to_click": int((t1 - t0) * 1000),
        "click_to_textbox": int((t2 - t1) * 1000),
        "textbox_to_submit": int((t3 - t2) * 1000),
    }
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
    grp = Group(status.get("spinner", Text("")), table, summary)
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
    cfg = load_config()
    global SEARCH_URL
    SEARCH_URL = build_search_url(cfg)

    ensure_hf_token()

    pos_kws = cfg.get("positive_keywords", [])
    neg_kws = cfg.get("negative_keywords", [])
    reply_msg = cfg.get("reply_message", "")

    ai_enabled = cfg.get("ai_enabled", False)
    ai_model = cfg.get("ai_model", "joeddav/xlm-roberta-large-xnli")
    ai_labels = cfg.get("ai_candidate_labels", ["pembeli", "penjual", "lainnya"])
    ai_threshold = cfg.get("ai_threshold", 0.8)
    ai_timeout = cfg.get("ai_timeout_ms", 4000)
    pre_filter = cfg.get("pre_filter_keywords", True)
    log_preds = cfg.get("log_predictions", True)

    scan_cfg = cfg["scan"]
    net_cfg = cfg["network"]
    reply_cfg = cfg["reply"]

    ai_client = ZeroShotClient(ai_model, timeout_ms=ai_timeout) if ai_enabled else None

    replied = set(load_replied())
    seen_ids: set[int] = set()
    stats: Dict[str, int] = {}
    timers: Dict[str, List[int]] = {"scan_cycle": []}
    state = {"paused": False, "force_refresh": False, "dry_run": reply_cfg.get("dry_run", False), "quit": False}
    last_activity: Optional[tuple[str, datetime]] = None

    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR, headless=False, args=["--start-maximized"]
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    # buka halaman login dan tunggu sampai benar-benar masuk
    await resilient_goto(page, "https://x.com/login", net_cfg, stats)
    login_spinner = Spinner("dots", text="Menunggu login…")
    with Live(login_spinner, console=console, refresh_per_second=10):
        logged = await wait_until_logged_in(page, 120000)
    if not logged:
        console.print("[bold red]Gagal login.[/]")
        await browser.close()
        await pw.stop()
        return

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
                            continue
                        label = "pembeli"
                        conf = 1.0
                        if ai_enabled and ai_client:
                            res = await ai_client.classify(norm_text, ai_labels)
                            if res is None:
                                stats["ai_disabled"] = stats.get("ai_disabled", 0) + 1
                                logging.warning("AI classification unavailable; proceeding without filter")
                            else:
                                label, conf = res
                                if label != "pembeli" or conf < ai_threshold:
                                    stats["ai_amb"] = stats.get("ai_amb", 0) + 1
                                    continue

                        res = await attempt_reply(page, cand, reply_msg, reply_cfg, state, replied, stats, timers)
                        record_decision(cand, res)
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
            live.update(render_dashboard(stats, timers, status))

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

