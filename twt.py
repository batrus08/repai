#!/usr/bin/env python3
import sys, os, json, asyncio
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

# ---- KONFIGURASI ----
SEARCH_URL   = (
    "https://x.com/search?"
    "q=chatgpt%20%23zonauang&"
    "src=recent_search_click&"
    "f=live"
)
CONFIG_PATH  = "bot_config.json"
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
        ("🔑 Skip KW",    stats["kw"]),
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
    replied = load_replied()
    last_id = 0
    stats   = {k:0 for k in ("scanned","cand","replied","age","kw","btn","dis")}

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
                    text = (await art.inner_text()).lower()
                    if not any(k in text for k in pos_kws) or any(n in text for n in neg_kws):
                        stats["kw"] += 1
                        continue
                    btn = await art.query_selector("[data-testid='reply']")
                    if not btn:
                        stats["btn"] += 1
                        continue
                    if await btn.get_attribute("aria-disabled") == "true":
                        stats["dis"] += 1
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
