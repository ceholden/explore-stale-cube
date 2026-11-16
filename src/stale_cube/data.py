"""Synthetic monthly single-band COG archive, standing in for Landsat/HLS scenes."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from stale_cube.config import BUCKET, boto3_client

N_TIMES = 100
SIZE = 1024
TILE = 256
TIMES = pd.date_range("2017-01-01", periods=N_TIMES, freq="MS")
REPUBLISH_OFFSET = 5000

_TRANSFORM = from_origin(500000.0, 4000000.0, 30.0, 30.0)
_CRS = "EPSG:32618"


def expected_value(t: int, offset: int = 0) -> int:
    """Value of pixel (y=5, x=5) at time step t; seasonal curve plus a trend."""
    month = TIMES[t].month
    return int(1000 + 300 * np.sin(2 * np.pi * (month - 1) / 12) + 2 * t) + offset


def make_band(t: int, offset: int = 0) -> np.ndarray:
    """expected_value plus a spatial ramp and seeded noise; pixel (5, 5) is exact.

    The noise depends on (t, offset), so a republished scene compresses to
    different tile sizes and its byte layout shifts, as real reprocessing does.
    """
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ramp = ((yy + xx) // 64).astype(np.int32)
    ramp -= ramp[5, 5]
    block = np.random.default_rng([t, offset]).integers(0, 50, (64, 64), dtype=np.int32)
    noise = np.tile(block, (SIZE // 64, SIZE // 64))
    noise[5, 5] = 0
    return (expected_value(t, offset) + ramp + noise).astype(np.uint16)


def key_for(prefix: str, t: int) -> str:
    return f"{prefix}/cogs/scene_{TIMES[t]:%Y%m}.tif"


def url_for(prefix: str, t: int) -> str:
    return f"s3://{BUCKET}/{key_for(prefix, t)}"


def cog_bytes(t: int, offset: int = 0) -> bytes:
    profile = dict(
        driver="COG",
        width=SIZE,
        height=SIZE,
        count=1,
        dtype="uint16",
        crs=_CRS,
        transform=_TRANSFORM,
        blocksize=TILE,
        compress="DEFLATE",
        level=1,
        overviews="NONE",
    )
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(make_band(t, offset), 1)
        return mem.read()


def write_cog(prefix: str, t: int, offset: int = 0) -> str:
    boto3_client().put_object(Bucket=BUCKET, Key=key_for(prefix, t), Body=cog_bytes(t, offset))
    return url_for(prefix, t)


def write_archive(prefix: str) -> list[str]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda t: write_cog(prefix, t), range(N_TIMES)))


def republish(prefix: str, t: int) -> str:
    """Overwrite scene t in place with different values, as a reprocessing would."""
    return write_cog(prefix, t, offset=REPUBLISH_OFFSET)


def delete(prefix: str, t: int) -> None:
    boto3_client().delete_object(Bucket=BUCKET, Key=key_for(prefix, t))


def etag(prefix: str, t: int) -> str:
    return etag_url(url_for(prefix, t))


def etag_url(url: str) -> str:
    key = url.removeprefix(f"s3://{BUCKET}/")
    return boto3_client().head_object(Bucket=BUCKET, Key=key)["ETag"]


__all__ = [
    "N_TIMES",
    "REPUBLISH_OFFSET",
    "SIZE",
    "TILE",
    "TIMES",
    "cog_bytes",
    "delete",
    "etag",
    "etag_url",
    "expected_value",
    "key_for",
    "make_band",
    "republish",
    "url_for",
    "write_archive",
    "write_cog",
]
