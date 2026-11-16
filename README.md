# stale-cube

What happens to an Xarray read of a virtual Icechunk cube when the COGs it points
at are republished or deleted (as Landsat/HLS do)?

## Run it

```bash
docker compose up -d                 # moto S3 on localhost:5000
uv sync
uv run jupyter lab notebooks/stale_virtual_store.ipynb
```

Or headless:

```bash
uv run jupyter nbconvert --execute --to notebook --inplace notebooks/stale_virtual_store.ipynb
uv run pytest -q                     # same scenarios as assertions
```

The notebook resets the bucket at the start, so it can be rerun at any time.
Set `STALE_CUBE_ENDPOINT` to point at a different S3-compatible server.

## Layout

- `src/stale_cube/data.py` - synthetic monthly COG archive (100 x 1024x1024, 256x256 deflate tiles)
- `src/stale_cube/virtualize.py` - VirtualiZarr + virtual-tiff build, per-file ETag pinning
- `src/stale_cube/readers.py` - `strict`, `tolerant` (`TolerantStore`), `check` + `repair`, `ignore-checksum`
- `src/stale_cube/report.py` - plain-language outcomes for the summary table
- `docs/superpowers/` - design and plan

## Matrix

| axis | values |
|---|---|
| checksum in refs | `lastmod` (VirtualiZarr default), `etag` |
| archive change | republish scene t=40, delete scene t=70 |
| read mode | `strict`, `tolerant`, `ignore-checksum`, `check-then-repair` |
