from typing import Any


def unwrap(provider: Any) -> Any:
    """Helper to unwrap SafeProviderWrapper or similar proxies in tests."""
    return provider._provider if hasattr(provider, "_provider") else provider
