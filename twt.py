#!/usr/bin/env python3
import sys, os, json, asyncio, unicodedata
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright, TimeoutError
import psutil, pyfiglet
from urllib.parse import quote  # for URL encoding

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.align import Align
from rich.table import Table
from rich.text import Text

# Responda uses HF API via ZeroShotClient
from ai import ZeroShotClient

# ---- KONFIGURASI ----
CONFIG_PATH  = "bot_config.json"
REPLIED_LOG  = "replied_ids.json"
SESSION_DIR  = "bot_session"
COOKIE_FILE  = "session.json"
MAX_AGE_HRS  = 3    # hanya balas tweet ≤ 3 jam
LOOP_SEC     = 15   # jeda antar loop (detik)

# Default search URL example (will be rebuilt from config)
SEARCH_URL = "https://x.com/search?q=chatgpt%20%23zonauang&src=recent_search_click&f=live"

console = Console()

def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError:
        return None

def load_config():
    cfg = load_json(CONFIG_PATH)
    if not isinstance(cfg, dict):
        console.print(f"[bold red]ERROR:[/] Gagal baca/parse `{CONFIG_PATH}`.")
        sys.exit(1)
    return cfg


def build_search_url(cfg: dict) -> str:
    """Bentuk URL pencarian X berdasarkan konfigurasi."""
    sc = cfg.get("search_config") or {}

    keyword = sc.get("keyword", "chatgpt")
    hashtag = sc.get("hashtag", "zonauang")
    src = sc.get("src", "recent_search_click")
    live = sc.get("live", True)

    query = quote(f"{keyword} #{hashtag}")  # encode spasi dan tanda '#'
    url = f"https://x.com/search?q={query}&src={quote(src)}"
    if live:
        url += "&f=live"
    return url

def load_replied():
    data = load_json(REPLIED_LOG)
    return data if isinstance(data, list) else []

def save_replied(ids):
    json.dump(ids, open(REPLIED_LOG, "w", encoding="utf-8"), indent=2)

async def wait_for_manual_captcha():
    console.print("[bold yellow]⚠️ CAPTCHA terdeteksi![/] Selesaikan di browser lalu tekan Enter…")
    await asyncio.to_thread(input)

async def detect_captcha(page, stats=None):
    u = page.url.lower()
    if "captcha" in u or "challenge" in u:
        if stats is not None:
            stats["captcha"] += 1
        await wait_for_manual_captcha()
    else:
        try:
            if await page.query_selector("iframe[src*='captcha']"):
                if stats is not None:
                    stats["captcha"] += 1
                await wait_for_manual_captcha()
        except TimeoutError:
            pass

async def detect_rate_limit(page, stats=None):
    try:
        if await page.query_selector("text='Rate limit exceeded'"):
            if stats is not None:
                stats["rate"] += 1
            return True
    except TimeoutError:
        pass
    return False

def render_banner() -> Text:
    art = pyfiglet.figlet_format("Meowlie Bot", font="slant")
    width = console.size.width
    centered = "\n".join(line.center(width) for line in art.splitlines())
    return Text(centered, style="bold green")

def render_summary(stats, last_activity) -> Group:
    table = Table(
        expand=True,
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        show_lines=False
    )
    cols = [
        ("🔍 Ditemukan", stats["scanned"]),
        ("🎯 Calon Balas", stats["cand"]),
        ("✅ Sudah Balas", stats["replied"]),
        ("🪤 Skip Kata", stats["kw"]),
        ("🚫 Skip Tombol", stats["btn"]),
    ]
    for header, _ in cols:
        table.add_column(header, justify="center")
    table.add_row(*(str(v) for _, v in cols))

    lines = [f"Terlalu Lama: {stats['age']}"]
    if last_activity:
        user, ts = last_activity
        lines.append(f"Aktivitas terakhir: @{user} - {ts.strftime('%H:%M:%S')}")
    else:
        lines.append("Aktivitas terakhir: -")
    if stats.get("captcha"):
        lines.append(f"Captcha: {stats['captcha']}")
    if stats.get("rate"):
        lines.append(f"Rate-limit: {stats['rate']}")
    if stats.get("ai_err"):
        lines.append(f"AI error: {stats['ai_err']}")

    return Group(table, Text("\n".join(lines), style="dim"))

def render_system() -> Table:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    table = Table(
        expand=True,
        box=box.SIMPLE_HEAVY,
        show_header=False
    )
    table.add_column("", justify="center")
    table.add_column("", justify="center")
    table.add_row(f"🖥️ CPU {cpu:.1f}%", f"💾 RAM {mem:.1f}%")
    return table

def render_marquee(offset: int) -> Text:
    msg = "😻 Meowlie Bot · Balas Otomatis #zonauang 😻"
    width = console.size.width - 4
    s = msg + "   "
    big = s * ((width // len(s)) + 2)
    pos = offset % len(s)
    return Text(big[pos:pos+width], style="dim")


def normalize_text(text: str) -> str:
    """Normalisasi unicode dan huruf kecil."""
    return unicodedata.normalize("NFKC", text).lower().strip()


def passes_prefilter(text: str, pos_keywords, neg_keywords) -> bool:
    """Saring cepat berdasarkan keyword positif/negatif."""
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


def log_ai_decision(tid, label, conf, prefilter_pass, reason, text, path="ai_decisions.log"):
    data = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "tweet_id": tid,
        "label": label,
        "confidence": conf,
        "prefilter_pass": prefilter_pass,
        "reason": reason,
        "text_sample": text[:200],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass

def build_layout(stats, timer, offset, last_activity) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=7),
        Layout(name="body",   ratio=2),
        Layout(name="footer", size=3),
    )

    # HEADER
    banner = render_banner()
    now    = datetime.now().strftime("%H:%M:%S")
    wpanel = Panel(now, title="⏰ Waktu", border_style="bright_blue")
    tpanel = Panel(f"{timer}s", title="⏳ Timer", border_style="bright_yellow")
    row = Layout()
    row.split_row(Layout(wpanel, ratio=1), Layout(tpanel, ratio=1))

    layout["header"].update(
        Panel(
            Group(
                Align.center(banner),
                Align.center(row)
            ),
            border_style="bright_green",
            padding=0
        )
    )

    # BODY
    summary_pan = Panel(render_summary(stats, last_activity),
                        title="🐾 Summary",
                        border_style="cyan",
                        padding=(1,1))
    system_pan  = Panel(render_system(),
                        title="⚙️ System",
                        border_style="magenta",
                        padding=(1,1))
    body = Layout()
    body.split_row(
        Layout(summary_pan, ratio=3),
        Layout(system_pan,  ratio=2),
    )
    layout["body"].update(body)

    # FOOTER
    layout["footer"].update(
        Panel(render_marquee(offset),
              border_style="bright_black",
              padding=(0,1))
    )

    return layout

async def run():
    cfg = load_config()
    global SEARCH_URL
    SEARCH_URL = build_search_url(cfg)  # bangun URL pencarian dari config
    pos_kws = cfg.get("positive_keywords", [])
    neg_kws = cfg.get("negative_keywords", [])
    reply   = cfg.get("reply_message", "")

    ai_enabled   = cfg.get("ai_enabled", False)
    ai_model     = cfg.get("ai_model", "joeddav/xlm-roberta-large-xnli")
    ai_labels    = cfg.get("ai_candidate_labels", ["pembeli", "penjual", "lainnya"])
    ai_threshold = cfg.get("ai_threshold", 0.8)
    ai_timeout   = cfg.get("ai_timeout_ms", 4000)
    pre_filter   = cfg.get("pre_filter_keywords", True)
    log_preds    = cfg.get("log_predictions", True)
    dry_run      = cfg.get("dry_run", False)

    ai_client = ZeroShotClient(ai_model, timeout_ms=ai_timeout) if ai_enabled else None

    replied = load_replied()
    last_id = 0
    last_activity = None
    stats   = {k:0 for k in ("scanned","cand","replied","age","kw","btn","ai_calls","ai_skip","ai_amb","ai_err","captcha","rate")}

    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        headless=False,
        args=["--start-maximized"]
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    if not os.path.exists(COOKIE_FILE):
        console.print("[green]🔐 Silakan login ke X…[/]")
        await page.goto("https://x.com/login")
        try:
            await page.wait_for_selector("[data-testid='AppTabBar_Profile_Link']", timeout=120000)
            await page.context.storage_state(path=COOKIE_FILE)
        except TimeoutError:
            console.print("[red]Gagal memverifikasi login.[/]")
    else:
        await page.goto("https://x.com/home")
        try:
            await page.wait_for_selector("[data-testid='AppTabBar_Profile_Link']", timeout=15000)
        except TimeoutError:
            console.print("[red]⚠️ Verifikasi login gagal.[/]")

    await page.goto(SEARCH_URL, wait_until="domcontentloaded")
    await detect_captcha(page, stats)
    await detect_rate_limit(page, stats)
    await asyncio.sleep(1)

    layout = build_layout(stats, LOOP_SEC, offset=0, last_activity=last_activity)
    with Live(layout, console=console, screen=True, refresh_per_second=10) as live:
        offset = 0
        while True:
            try:
                arts = await page.query_selector_all("article")
                stats["scanned"] += len(arts)

                items = []
                for art in arts:
                    link = await art.query_selector("a[href*='/status/']")
                    if not link:
                        continue
                    href = await link.get_attribute("href")
                    tid = int(href.split("/")[-1])
                    user = href.split("/")[1]
                    if tid <= last_id or tid in replied:
                        continue
                    tm = await art.query_selector("time")
                    if not tm:
                        continue
                    ts = await tm.get_attribute("datetime")
                    ttime = datetime.fromisoformat(ts.replace("Z","+00:00"))
                    items.append((ttime, tid, art, user))

                items.sort(key=lambda x: x[0], reverse=True)
                stats["cand"] += len(items)

                for ttime, tid, art, user in items:
                    last_id = max(last_id, tid)
                    if datetime.now(timezone.utc) - ttime > timedelta(hours=MAX_AGE_HRS):
                        stats["age"] += 1
                        continue

                    raw_text = await art.inner_text()
                    norm_text = normalize_text(raw_text)
                    if len(norm_text) < 5:
                        stats["kw"] += 1
                        if log_preds:
                            log_ai_decision(tid, None, 0.0, False, "too_short", norm_text)
                        continue
                    if pre_filter and not passes_prefilter(norm_text, pos_kws, neg_kws):
                        stats["kw"] += 1
                        if log_preds:
                            log_ai_decision(tid, None, 0.0, False, "prefilter_fail", norm_text)
                        continue

                    btn = await art.query_selector("[data-testid='reply']")
                    if not btn or await btn.get_attribute("aria-disabled") == "true":
                        stats["btn"] += 1
                        console.log(f"Skip Tombol: {tid}")
                        continue

                    label = None
                    conf = 0.0
                    reason = ""
                    prefilter_pass = True

                    if ai_enabled:
                        if not ai_client:
                            stats["ai_skip"] += 1
                            prefilter_pass = passes_prefilter(norm_text, pos_kws, neg_kws)
                            if prefilter_pass:
                                label, conf, reason = "pembeli", 1.0, "balas_ok"
                            else:
                                stats["kw"] += 1
                                reason = "prefilter_fail"
                        else:
                            stats["ai_calls"] += 1
                            res = await ai_client.classify(norm_text, ai_labels)
                            if res is None:
                                stats["ai_err"] += 1
                                prefilter_pass = passes_prefilter(norm_text, pos_kws, neg_kws)
                                if prefilter_pass:
                                    label, conf, reason = "pembeli", 1.0, "balas_ok"
                                else:
                                    reason = "ai_error"
                            else:
                                label, conf = res
                                if label == "pembeli" and conf >= ai_threshold:
                                    reason = "balas_ok"
                                else:
                                    stats["ai_amb"] += 1
                                    reason = "ai_conf_low"
                    else:
                        prefilter_pass = passes_prefilter(norm_text, pos_kws, neg_kws)
                        if prefilter_pass:
                            label, conf, reason = "pembeli", 1.0, "balas_ok"
                        else:
                            stats["kw"] += 1
                            reason = "prefilter_fail"

                    if reason != "balas_ok":
                        if log_preds:
                            log_ai_decision(tid, label, conf, prefilter_pass, reason, norm_text)
                        continue

                    if dry_run:
                        if log_preds:
                            log_ai_decision(tid, label, conf, prefilter_pass, "dry_run", norm_text)
                        continue

                    await btn.click()
                    await detect_captcha(page, stats)
                    await detect_rate_limit(page, stats)
                    try:
                        await page.wait_for_selector("div[role='textbox']", timeout=5000)
                    except TimeoutError:
                        continue

                    await page.fill("div[role='textbox']", reply)
                    await page.click("[data-testid='tweetButton']")
                    replied.append(tid); save_replied(replied)
                    stats["replied"] += 1
                    last_activity = (user, datetime.now())
                    if log_preds:
                        log_ai_decision(tid, label, conf, True, "balas_ok", norm_text)

            except Exception as e:
                console.print(f"[bold red]FATAL:[/] {e}")
                break

            for rem in range(LOOP_SEC, 0, -1):
                layout = build_layout(stats, rem, offset, last_activity)
                live.update(layout)
                await asyncio.sleep(1)
                offset += 1

            await page.goto(SEARCH_URL, wait_until="domcontentloaded")
            await detect_captcha(page, stats)
            await detect_rate_limit(page, stats)
            await asyncio.sleep(1)

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
