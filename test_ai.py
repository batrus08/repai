"""Unit tests for the ai module."""

import pytest
from unittest.mock import AsyncMock

from ai import classify_text

# Menggunakan pytest-asyncio untuk menandai tes sebagai asynchronous
pytestmark = pytest.mark.asyncio


async def test_classify_text_max_tokens(mocker):
    """
    Verifies that classify_text calls ask_ai with max_tokens=16.
    """
    # Mock ask_ai to prevent actual API calls
    mock_ask_ai = mocker.patch("ai.ask_ai", new_callable=AsyncMock)

    # Expected text for classification
    test_text = "Saya mau beli dong"

    # Call the function under test
    await classify_text(test_text)

    # Verify that ask_ai was called with the correct parameters
    mock_ask_ai.assert_called_once()
    args, kwargs = mock_ask_ai.call_args
    assert kwargs.get("max_tokens") == 16, "max_tokens should be set to 16"
