"""Four ways to read a possibly-stale virtual cube.

- strict: plain xarray read; any stale chunk raises.
- tolerant: wrap the store so stale/missing source chunks become fill values,
  and report which time steps were skipped.
- check: HEAD every source file and compare to the ETags recorded at build time,
  then `repair` commits refreshed refs (or removes refs for deleted files).
- ignore_checksum: rewrite refs without checksums in a throwaway session, i.e.
  what a reader-side "read it anyway" override would return.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field

import icechunk
import numpy as np
import xarray as xr
from botocore.exceptions import ClientError
from virtual_tiff import VirtualTIFF
from virtualizarr import open_virtual_dataset
from zarr.abc.store import ByteRequest
from zarr.core.buffer import Buffer, BufferPrototype
from zarr.storage import WrapperStore

from stale_cube import config, data
from stale_cube.virtualize import (
    ARRAY,
    manifest_entries,
    pin_etags,
    recorded_etags,
    write_refs,
)

log = logging.getLogger(__name__)

PIXEL = dict(y=5, x=5)


@dataclass
class ReadResult:
    mode: str
    values: np.ndarray | None = None
    skipped: dict[int, str] = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None

    @property
    def mask(self) -> np.ndarray | None:
        """True where the time step was skipped because its source chunk was stale."""
        if self.values is None:
            return None
        m = np.zeros(self.values.shape[0], dtype=bool)
        m[list(self.skipped)] = True
        return m


def _capture(mode: str, exc: Exception) -> ReadResult:
    return ReadResult(
        mode=mode,
        error=f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
        error_kind=str(getattr(exc, "kind", "")) or None,
    )


def stale_reason(exc: Exception) -> str | None:
    """Classify an icechunk error as a stale source chunk, or None if it is something else."""
    kind = getattr(exc, "kind", None)
    if kind == icechunk.ErrorKind.VIRTUAL_CHUNK_MODIFIED:
        return "modified"
    if kind == icechunk.ErrorKind.VIRTUAL_CHUNK_FETCH and (
        "NoSuchKey" in str(exc) or "404" in str(exc)
    ):
        return "missing"
    return None


def _time_index(key: str) -> int | None:
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == ARRAY and parts[1] == "c":
        return int(parts[2])
    return None


class TolerantStore(WrapperStore):
    """Return "missing" for chunks whose virtual source changed or vanished.

    Zarr then fills those chunks with the array fill value, which decodes to NaN.
    Any other error (credentials, throttling, network) still raises.
    """

    def __init__(self, store) -> None:
        super().__init__(store)
        self.skipped: dict[str, str] = {}
        self._lock = threading.Lock()

    async def get(
        self, key: str, prototype: BufferPrototype, byte_range: ByteRequest | None = None
    ) -> Buffer | None:
        try:
            return await self._store.get(key, prototype, byte_range)
        except icechunk.IcechunkError as exc:
            reason = stale_reason(exc)
            if reason is None:
                raise
            log.warning("skipping %s chunk %s: %s", reason, key, str(exc).splitlines()[0])
            with self._lock:
                self.skipped[key] = reason
            return None

    def skipped_times(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for key, reason in self.skipped.items():
            t = _time_index(key)
            if t is not None:
                out[t] = reason
        return out


def open_cube(store, chunks=None) -> xr.Dataset:
    return xr.open_zarr(store, consolidated=False, chunks=chunks)


def read_strict(repo: icechunk.Repository, sel: dict = PIXEL) -> ReadResult:
    store = repo.readonly_session("main").store
    try:
        values = open_cube(store)[ARRAY].isel(**sel).values
    except Exception as exc:
        return _capture("strict", exc)
    return ReadResult("strict", values=values)


def read_tolerant(repo: icechunk.Repository, sel: dict = PIXEL, chunks=None) -> ReadResult:
    store = TolerantStore(repo.readonly_session("main").store)
    try:
        values = open_cube(store, chunks=chunks)[ARRAY].isel(**sel).compute().values
    except Exception as exc:
        return _capture("tolerant", exc)
    return ReadResult("tolerant", values=values, skipped=store.skipped_times())


def check(repo: icechunk.Repository, prefix: str) -> dict[int, str]:
    """HEAD every source scene; report time steps whose file changed or is gone.

    Changes are only detectable when the writer recorded ETags; without them only
    deletions show up.
    """
    recorded = recorded_etags(repo.readonly_session("main").store) or {}
    s3 = config.boto3_client()
    stale: dict[int, str] = {}
    for t in range(data.N_TIMES):
        url = data.url_for(prefix, t)
        try:
            current = s3.head_object(Bucket=config.BUCKET, Key=data.key_for(prefix, t))["ETag"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                stale[t] = "missing"
                continue
            raise
        if url in recorded and recorded[url] != current:
            stale[t] = "modified"
    return stale


def repair(repo: icechunk.Repository, prefix: str, stale: dict[int, str]) -> str | None:
    """Re-virtualize modified scenes and drop refs for deleted ones, in one commit."""
    if not stale:
        return None
    session = repo.writable_session("main")
    store = session.store
    has_etags = recorded_etags(store) is not None
    registry = config.obstore_registry()
    n_tiles = data.SIZE // data.TILE
    for t, reason in sorted(stale.items()):
        if reason == "missing":
            for y in range(n_tiles):
                for x in range(n_tiles):
                    _run(store.delete(f"{ARRAY}/c/{t}/{y}/{x}"))
            continue
        url = data.url_for(prefix, t)
        scene = open_virtual_dataset(url, registry=registry, parser=VirtualTIFF(ifd=0))
        entries = manifest_entries(scene.rename({"0": ARRAY}).expand_dims("time"), t_offset=t)
        if has_etags:
            pin_etags(store, entries)
        else:
            write_refs(store, entries)
    return session.commit(f"repair stale scenes: {sorted(stale.items())}")


def read_ignore_checksum(repo: icechunk.Repository, sel: dict = PIXEL) -> ReadResult:
    """Strip checksums from every ref in an uncommitted session, then read."""
    session = repo.writable_session("main")
    store = session.store
    specs = [
        icechunk.VirtualChunkSpec(
            index=[int(c) for c in coord], location=path, offset=int(off), length=int(ln)
        )
        for coord, path, off, ln in _list_virtual_refs(store)
    ]
    store.set_virtual_refs(ARRAY, specs)
    try:
        values = open_cube(store)[ARRAY].isel(**sel).values
    except Exception as exc:
        result = _capture("ignore-checksum", exc)
    else:
        result = ReadResult("ignore-checksum", values=values)
    session.discard_changes()
    return result


def _list_virtual_refs(store) -> list[tuple]:
    async def collect():
        out = []
        it = store.array_chunk_iterator(f"/{ARRAY}", 10_000)
        async for coords, kinds, paths, offsets, lengths, _inlined in it:
            for i in range(len(paths)):
                if int(kinds[i]) == int(icechunk.ChunkType.virtual):
                    out.append((coords[i], paths[i], offsets[i], lengths[i]))
        return out

    return _run(collect())


def _run(coro):
    """Run a coroutine to completion even when called from inside Jupyter's event loop."""
    result: dict = {}

    def target():
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            result["error"] = exc

    th = threading.Thread(target=target)
    th.start()
    th.join()
    if "error" in result:
        raise result["error"]
    return result["value"]
