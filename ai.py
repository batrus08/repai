import time
import asyncio
import requests
from typing import List, Optional, Tuple

class ZeroShotClient:
    """Client sederhana untuk Hugging Face zero-shot classification API."""

    def __init__(self, model: str, token: str, timeout_ms: int = 4000, min_delay: float = 1.0):
        self.model = model
        self.token = token
        self.timeout = timeout_ms / 1000
        self.min_delay = min_delay
        self._last_call = 0.0

    async def _throttle(self) -> None:
        """Pastikan ada jeda minimum antar panggilan API."""
        delta = time.time() - self._last_call
        if delta < self.min_delay:
            await asyncio.sleep(self.min_delay - delta)
        self._last_call = time.time()

    async def classify(self, text: str, candidate_labels: List[str]) -> Optional[Tuple[str, float]]:
        """
        Klasifikasikan *text* terhadap *candidate_labels*.
        Mengembalikan tuple (label, confidence) atau None jika gagal.
        """
        await self._throttle()
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {
            "inputs": text,
            "parameters": {"candidate_labels": candidate_labels},
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
                return None
            labels = data.get("labels")
            scores = data.get("scores")
            if labels and scores:
                return labels[0], scores[0]
        except Exception:
            return None
        return None
