import os
import time
import asyncio
import logging
from typing import Optional

import requests

API_URL = "https://api.openai.com/v1/chat/completions"
RETRY_STATUS = {429, 500, 502, 503, 504}
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
PRICE_PER_1K_TOKENS = 0.0005  # USD, estimasi kasar


async def ask_ai(system_msg: str, user_msg: str, *, max_tokens: int = 16, timeout: float = 15.0) -> Optional[str]:
    """Kirim pesan ke OpenAI Chat Completions API dan kembalikan konten balasan."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logging.warning("OPENAI_API_KEY tidak ditemukan; melewati panggilan AI")
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }

    backoff = 1
    for attempt in range(5):
        start = time.perf_counter()
        try:
            resp = await asyncio.to_thread(
                requests.post, API_URL, headers=headers, json=payload, timeout=timeout
            )
            if resp.status_code in RETRY_STATUS:
                raise RuntimeError(f"Temporary error {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            cost = total_tokens / 1000 * PRICE_PER_1K_TOKENS
            elapsed = time.perf_counter() - start
            logging.info(
                "OpenAI tokens=%d cost_est=$%.6f time=%.2fs", total_tokens, cost, elapsed
            )
            return content
        except Exception as e:
            logging.warning("OpenAI request failed: %s", e)
            if attempt == 4:
                return None
            await asyncio.sleep(backoff)
            backoff *= 2
    return None


async def classify_text(text: str, *, timeout_ms: int = 4000) -> Optional[str]:
    """Klasifikasikan teks menjadi 'penjual', 'pembeli', atau 'lainnya'."""
    system_msg = (
        "Kamu mengklasifikasikan teks menjadi 'penjual', 'pembeli', atau 'lainnya'. "
        "Jawab hanya salah satu kata itu."
    )
    return await ask_ai(system_msg, text, max_tokens=1, timeout=timeout_ms / 1000)
