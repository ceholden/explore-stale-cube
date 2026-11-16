import urllib.request

import pytest

from stale_cube.config import ENDPOINT, reset_bucket


@pytest.fixture(scope="session", autouse=True)
def moto():
    try:
        urllib.request.urlopen(ENDPOINT, timeout=2)
    except OSError:
        pytest.skip(f"moto not reachable at {ENDPOINT}; run `docker compose up -d`")
    reset_bucket()
