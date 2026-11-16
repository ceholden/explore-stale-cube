"""Virtualize the COG archive into an Icechunk repo, optionally pinning per-file ETags."""

from typing import Literal

import icechunk
import xarray as xr
import zarr
from virtual_tiff import VirtualTIFF
from virtualizarr import open_virtual_dataset

from stale_cube import config, data

Checksum = Literal["lastmod", "etag"]
ARRAY = "band"
NODATA = 0
ETAGS_ATTR = "source_etags"


def _s3_options() -> dict:
    return dict(
        endpoint_url=config.ENDPOINT,
        allow_http=True,
        force_path_style=True,
        region=config.REGION,
    )


def _credentials() -> icechunk.S3Credentials:
    return icechunk.s3_credentials(
        access_key_id=config.ACCESS_KEY, secret_access_key=config.SECRET_KEY
    )


def repo_config() -> icechunk.RepositoryConfig:
    cfg = icechunk.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            f"s3://{config.BUCKET}/", icechunk.s3_store(**_s3_options())
        )
    )
    return cfg


def open_repo(prefix: str, create: bool = False) -> icechunk.Repository:
    storage = icechunk.s3_storage(
        bucket=config.BUCKET,
        prefix=f"{prefix}/repo",
        access_key_id=config.ACCESS_KEY,
        secret_access_key=config.SECRET_KEY,
        **_s3_options(),
    )
    vcc_creds = icechunk.containers_credentials({f"s3://{config.BUCKET}/": _credentials()})
    opener = icechunk.Repository.create if create else icechunk.Repository.open
    return opener(storage, config=repo_config(), authorize_virtual_chunk_access=vcc_creds)


def virtual_dataset(urls: list[str]) -> xr.Dataset:
    """One virtual (time, y, x) dataset with a single `band` variable."""
    registry = config.obstore_registry()
    parser = VirtualTIFF(ifd=0)
    scenes = [
        open_virtual_dataset(u, registry=registry, parser=parser).rename({"0": ARRAY})
        for u in urls
    ]
    vds = xr.concat(
        [s.expand_dims("time") for s in scenes],
        dim="time",
        coords="minimal",
        compat="override",
        combine_attrs="override",
    )
    vds = vds.assign_coords(time=data.TIMES[: len(urls)])
    vds[ARRAY].attrs["_FillValue"] = NODATA
    return vds


RefKey = tuple[int, int, int]


def manifest_entries(vds: xr.Dataset, t_offset: int = 0) -> dict[RefKey, dict]:
    """(time, y, x) -> {path, offset, length} for the `band` manifest, shifted in time."""
    out = {}
    for chunk_key, entry in vds[ARRAY].data.manifest.dict().items():
        t, y, x = (int(c) for c in chunk_key.split("."))
        out[(t + t_offset, y, x)] = entry
    return out


def write_refs(
    store: icechunk.IcechunkStore,
    entries: dict[RefKey, dict],
    etags: dict[str, str] | None = None,
) -> None:
    """Set virtual refs, attaching each source file's ETag when `etags` is given."""
    specs = [
        icechunk.VirtualChunkSpec(
            index=list(key),
            location=e["path"],
            offset=int(e["offset"]),
            length=int(e["length"]),
            etag_checksum=None if etags is None else etags[e["path"]],
        )
        for key, e in entries.items()
    ]
    store.set_virtual_refs(ARRAY, specs, validate_containers=True)


def recorded_etags(store: icechunk.IcechunkStore) -> dict[str, str] | None:
    """The url -> ETag map the writer recorded, or None if it recorded none."""
    attrs = zarr.open_group(store, mode="r").attrs
    return dict(attrs[ETAGS_ATTR]) if ETAGS_ATTR in attrs else None


def pin_etags(store: icechunk.IcechunkStore, entries: dict[RefKey, dict]) -> None:
    """Re-set refs with their source file's current ETag and record it in group attrs.

    Icechunk stores the checksum per chunk ref but offers no API to read it back,
    and VirtualiZarr only writes a single last-modified timestamp, so ETags are
    attached here and mirrored into attrs for reader-side staleness checks.
    """
    urls = {e["path"] for e in entries.values()}
    fresh = {url: data.etag_url(url) for url in urls}
    write_refs(store, entries, fresh)
    etags = recorded_etags(store) or {}
    etags.update(fresh)
    zarr.open_group(store, mode="r+").attrs[ETAGS_ATTR] = etags


def build_cube(prefix: str, urls: list[str], checksum: Checksum) -> str:
    """Virtualize `urls` into a new repo under `prefix`.

    `lastmod` is VirtualiZarr's default: every ref gets a last-modified checksum of
    roughly the write time. `etag` additionally re-sets each ref with its file's ETag.
    """
    repo = open_repo(prefix, create=True)
    session = repo.writable_session("main")
    vds = virtual_dataset(urls)
    vds.vz.to_icechunk(session.store)
    if checksum == "etag":
        pin_etags(session.store, manifest_entries(vds))
    return session.commit(f"virtualize {len(urls)} scenes, checksum={checksum}")
