import os
import time
import asyncio
import logging
from typing import Optional

from openai import OpenAI, OpenAIError

RETRY_STATUS = {429, 500, 502, 503, 504}
DEFAULT_MODEL = "gpt-5-nano"
PRICE_PER_1K_TOKENS = 0.0005  # USD, estimasi kasar
CLIENT: Optional[OpenAI] = None


def _get_model_name() -> str:
    """Ambil nama model terbaru dari variabel lingkungan."""
    model = os.getenv("OPENAI_MODEL", "").strip()
    return model or DEFAULT_MODEL


async def ask_ai(system_msg: str, user_msg: str, *, max_tokens: int = 16, timeout: float = 15.0) -> Optional[str]:
    """Kirim pesan ke OpenAI Chat Completions API dan kembalikan konten balasan."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logging.warning("OPENAI_API_KEY tidak ditemukan; melewati panggilan AI")
        return None

    global CLIENT
    if CLIENT is None:
        CLIENT = OpenAI(api_key=api_key)

    backoff = 1
    for attempt in range(5):
        start = time.perf_counter()
        try:
            resp = await asyncio.to_thread(
                CLIENT.chat.completions.create,
                model=_get_model_name(),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content.strip()
            usage = getattr(resp, "usage", None)
            total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
            cost = total_tokens / 1000 * PRICE_PER_1K_TOKENS
            elapsed = time.perf_counter() - start
            logging.info(
                "OpenAI tokens=%d cost_est=$%.6f time=%.2fs", total_tokens, cost, elapsed
            )
            return content
        except OpenAIError as e:
            status = getattr(e, "status_code", None)
            logging.warning("OpenAI request failed: %s", e)
            if attempt == 4 or status not in RETRY_STATUS:
                return None
            await asyncio.sleep(backoff)
            backoff *= 2
        except Exception as e:
            logging.warning("OpenAI request failed: %s", e)
            return None
    return None


async def classify_text(text: str, *, timeout_ms: int = 4000) -> Optional[str]:
    """Klasifikasikan teks menjadi 'penjual', 'pembeli', atau 'lainnya'."""
    system_msg = (
        "Kamu mengklasifikasikan teks menjadi 'penjual', 'pembeli', atau 'lainnya'. "
        "Jawab hanya salah satu kata itu."
    )
    return await ask_ai(system_msg, text, max_tokens=1, timeout=timeout_ms / 1000)
