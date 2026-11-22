import pytest

from app import main


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Every test starts with fresh limits, so tests never throttle each other."""
    main.ip_limiter.reset()
    main.daily_cap.reset()
    yield
