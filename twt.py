#!/usr/bin/env python3
import sys, os, json, asyncio, unicodedata
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright, TimeoutError
import psutil, pyfiglet

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.align import Align
from rich.table import Table
from rich.text import Text

from ai import ZeroShotClient

# ---- KONFIGURASI ----
SEARCH_URL   = (
    "https://x.com/search?"
    "q=chatgpt%20%23zonauang&"
    "src=recent_search_click&"
    "f=live"
)
CONFIG_PATH  = "bot_config.json"
TOKEN_PATH   = "tokens.json"
REPLIED_LOG  = "replied_ids.json"
SESSION_DIR  = "bot_session"
COOKIE_FILE  = "session.json"
MAX_AGE_HRS  = 3    # hanya balas tweet ≤ 3 jam
LOOP_SEC     = 15   # jeda antar loop (detik)

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

def load_tokens():
    data = load_json(TOKEN_PATH)
    return data if isinstance(data, dict) else {}

def load_replied():
    data = load_json(REPLIED_LOG)
    return data if isinstance(data, list) else []

def save_replied(ids):
    json.dump(ids, open(REPLIED_LOG, "w", encoding="utf-8"), indent=2)

async def wait_for_manual_captcha():
    console.print("[bold yellow]⚠️ CAPTCHA terdeteksi![/] Selesaikan di browser lalu tekan Enter…")
    await asyncio.to_thread(input)

async def detect_captcha(page):
    u = page.url.lower()
    if "captcha" in u or "challenge" in u:
        await wait_for_manual_captcha()
    else:
        try:
            if await page.query_selector("iframe[src*='captcha']"):
                await wait_for_manual_captcha()
        except TimeoutError:
            pass

def render_banner() -> Text:
    art = pyfiglet.figlet_format("Meowlie Bot", font="slant")
    width = console.size.width
    centered = "\n".join(line.center(width) for line in art.splitlines())
    return Text(centered, style="bold green")

def render_summary(stats) -> Table:
    table = Table(
        expand=True,
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        show_lines=False
    )
    cols = [
        ("🔍 Dipindai",   stats["scanned"]),
        ("🎯 Kandidat",   stats["cand"]),
        ("✅ Terbalas",   stats["replied"]),
        ("⏰ Lewat Usia", stats["age"]),
        ("🪤 Prefilter",  stats["kw"]),
        ("🤖 AI Call",    stats["ai_calls"]),
        ("🚫 AI Skip",    stats["ai_skip"]),
        ("⁉️ Ambigu",     stats["ai_amb"]),
        ("💥 AI Err",     stats["ai_err"]),
        ("🚫 Skip Btn",   stats["btn"]),
        ("✋ Skip Dis",    stats["dis"]),
    ]
    for header, _ in cols:
        table.add_column(header, justify="center")
    table.add_row(*(str(v) for _, v in cols))
    return table

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

def build_layout(stats, timer, offset) -> Layout:
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
    summary_pan = Panel(render_summary(stats),
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
    cfg     = load_config()
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

    tokens   = load_tokens()
    hf_token = tokens.get("hf_api_token")
    ai_client = None
    if ai_enabled and hf_token:
        ai_client = ZeroShotClient(ai_model, hf_token, timeout_ms=ai_timeout)

    replied = load_replied()
    last_id = 0
    stats   = {k:0 for k in ("scanned","cand","replied","age","kw","btn","dis","ai_calls","ai_skip","ai_amb","ai_err")}

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
        await asyncio.sleep(120)
        await page.context.storage_state(path=COOKIE_FILE)

    layout = build_layout(stats, LOOP_SEC, offset=0)
    with Live(layout, console=console, screen=True, refresh_per_second=10) as live:
        offset = 0
        while True:
            try:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded")
                await detect_captcha(page)
                await asyncio.sleep(1)

                arts = await page.query_selector_all("article")
                stats["scanned"] += len(arts)

                items = []
                for art in arts:
                    link = await art.query_selector("a[href*='/status/']")
                    if not link: continue
                    tid = int((await link.get_attribute("href")).split("/")[-1])
                    if tid <= last_id or tid in replied: continue
                    tm = await art.query_selector("time")
                    if not tm: continue
                    ts = await tm.get_attribute("datetime")
                    ttime = datetime.fromisoformat(ts.replace("Z","+00:00"))
                    items.append((ttime,tid,art))

                items.sort(key=lambda x: x[0], reverse=True)
                stats["cand"] += len(items)

                for ttime, tid, art in items:
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
                    if not btn:
                        stats["btn"] += 1
                        continue
                    if await btn.get_attribute("aria-disabled") == "true":
                        stats["dis"] += 1
                        continue

                    label = None
                    conf = 0.0
                    reason = ""

                    if ai_enabled:
                        if not ai_client:
                            stats["ai_skip"] += 1
                            reason = "ai_disabled"
                        else:
                            stats["ai_calls"] += 1
                            res = await ai_client.classify(norm_text, ai_labels)
                            if not res:
                                stats["ai_err"] += 1
                                reason = "error_ai"
                            else:
                                label, conf = res
                                if label == "pembeli" and conf >= ai_threshold:
                                    reason = "balas_ok"
                                else:
                                    stats["ai_amb"] += 1
                                    reason = "ambiguous"
                    else:
                        label, conf, reason = "pembeli", 1.0, "balas_ok"

                    if reason != "balas_ok":
                        if log_preds:
                            log_ai_decision(tid, label, conf, True, reason, norm_text)
                        continue

                    if dry_run:
                        if log_preds:
                            log_ai_decision(tid, label, conf, True, "dry_run", norm_text)
                        continue

                    await btn.click()
                    await detect_captcha(page)
                    try:
                        await page.wait_for_selector("div[role='textbox']", timeout=5000)
                    except TimeoutError:
                        continue

                    await page.fill("div[role='textbox']", reply)
                    await page.click("[data-testid='tweetButton']")
                    replied.append(tid); save_replied(replied)
                    stats["replied"] += 1
                    if log_preds:
                        log_ai_decision(tid, label, conf, True, "balas_ok", norm_text)

            except Exception as e:
                console.print(f"[bold red]FATAL:[/] {e}")
                break

            for rem in range(LOOP_SEC, 0, -1):
                layout = build_layout(stats, rem, offset)
                live.update(layout)
                await asyncio.sleep(1)
                offset += 1

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
