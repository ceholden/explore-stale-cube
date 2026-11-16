# stale-cube

What happens to an Xarray read of a virtual Icechunk cube when the COGs it points
at are republished or deleted (as Landsat/HLS do)?

## Use cases and motivation

Virtualizing data is a very powerful way to more easily represent time series while greatly
accelerating read performance by precomputing lookups to find the chunk you need to read.
This process comes at a cost, however, because it creates a potential point of inconsistency
if the data provider updates or deletes objects that exist in your virtual dataset. While
we would love to all live in a nicely behaved, "append only", world, many data providers do
not operate this way. For example:

- The Landsat archive (`s3://usgs-landsat`) publishes real-time ("RT") data that are replaced
  by higher quality, "T1" or "T2" standard processed data after some time. The "RT" data are
  deleted.
- The Harmonized Landsat Sentinel-2 (HLS) dataset may republish data with the same granule IDs
  if there are issues with the upstream data (e.g., geolocation errors in Sentinel-2) to fully
  populate a Sentinel-2 "twin" granule.
- Many weather forecasts only retain a "rolling" archive of the last 30 days. Most people don't
  want stale forecasts, so older files are deleted.

Icechunk includes a great mechanism for ensuring data safety: it can record the etag or the last
modified date per chunk, and refuses to serve a chunk whose source changed. Reads are verified
against these markers, and exit early rather than read incorrect values or fail to decode.
Out of the box, accessing such stores via tools like `xarray` would blow up a read that includes
stale or deleted chunks rather than be tolerant of invalid chunks within the cube that is
being `.compute()`-ed. The per-chunk check in Icechunk is the right level of granularity, but
the ecosystem using Icechunk has no notion of a chunk that failed but can be tolerated. 

The common pattern for dealing with the inconsistency between the virtual store and the original
data store is to update the virtual data store to reflect such changes, and Icechunk makes this
feasible in a distributed world through its transaction and version control. Ideally data providers
pair their dataset with a mechanism for notifications so these updates can be event driven, but
this isn't true in practice for many data providers. Even if updates are processed via events instead
of a scheduled check, there will still be a period of inconsistency that readers need to handle.

Depending on your use case, it may be preferable to be tolerant and return _something_ when
accessing the entire cube, rather than blowing up. Such use cases might include:

- Real-time ML serving APIs might prefer excluding one or two missing observations to provide
  an answer with increased uncertainty rather than having an outage.
- Exploratory science might be fine if a small subset of data have been replaced.
- Interactive time series plots could gap fill or mark the data as missing.

In the event that the virtual store is out of date, these use cases would benefit from knowing
the number of missing observations and the locations (space/time/etc). This could facilitate:

- Aborting if the number of missing observations is above a threshold.
- Updating uncertainty metrics associated with the data application.
- Emitting an event to trigger a virtual data store repair for the affected chunks.

This repo is a (heavily LLM assisted :robot:) exploration into how the ecosystem tooling works with
virtual data stores when the data provider has rug pulled the user.

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

## Matrix

| axis | values |
|---|---|
| checksum in refs | `lastmod` (VirtualiZarr default), `etag` |
| archive change | republish scene t=40, delete scene t=70 |
| read mode | `strict`, `tolerant`, `ignore-checksum`, `check-then-repair` |
