import time
import asyncio
import requests
import os
import logging
from typing import List, Optional, Tuple

class ZeroShotClient:
    """Client sederhana untuk Hugging Face zero-shot classification API."""

    def __init__(
        self,
        model: str,
        timeout_ms: int = 4000,
        min_delay: float = 1.0,
        hypothesis_template: str = "Teks ini menunjukkan bahwa {}.",
    ):
        self.model = model
        self.timeout = timeout_ms / 1000
        self.min_delay = min_delay
        self._last_call = 0.0
        self.hypothesis_template = hypothesis_template

    async def _throttle(self) -> None:
        """Pastikan ada jeda minimum antar panggilan API."""
        delta = time.time() - self._last_call
        if delta < self.min_delay:
            await asyncio.sleep(self.min_delay - delta)
        self._last_call = time.time()

    async def classify(
        self, text: str, candidate_labels: List[str]
    ) -> Optional[Tuple[str, float, float]]:
        """
        Klasifikasikan *text* terhadap *candidate_labels*.
        Mengembalikan tuple (label, top1, top2) atau None jika gagal.
        top1 = confidence label teratas, top2 = skor label kedua.
        """
        await self._throttle()
        token = os.environ.get("HF_API_TOKEN")
        if not token:
            logging.warning("HF_API_TOKEN tidak ditemukan; melewati klasifikasi")
            return None
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "inputs": text,
            "parameters": {
                "candidate_labels": candidate_labels,
                "hypothesis_template": self.hypothesis_template,
            },
        }
        url = f"https://api-inference.huggingface.co/models/{self.model}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.post(url, headers=headers, json=payload, timeout=self.timeout),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                logging.warning(f"HF API error: {data['error']}")
                return None
            labels = data.get("labels")
            scores = data.get("scores")
            if labels and scores:
                top1 = scores[0]
                top2 = scores[1] if len(scores) > 1 else 0.0
                return labels[0], top1, top2
        except Exception as e:
            logging.warning(f"ZeroShot classify error: {e}")
            return None
        return None
