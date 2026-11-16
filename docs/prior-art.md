# Prior art: stale virtual chunks in Icechunk / VirtualiZarr

Survey of GitHub discussion related to this experiment, as of 2026-11-13
(icechunk 2.3.1, virtualizarr 2.8.0). Searched issues and discussions in
`earth-mover/icechunk`, `zarr-developers/VirtualiZarr`, and `zarr-developers/zarr-python`.

## Summary

The concern is well documented, but every existing request is for turning
validation *off*. Nobody has asked for a reader-side *policy* on validation
failure (raise / fill / warn) that keeps reading and reports which chunks were
skipped. This experiment shows "off" is not useful for republished COGs: tile
offsets shift and the old refs fail to decode.

## Icechunk

### [#1488 Option to disable etag/checksum check](https://github.com/earth-mover/icechunk/issues/1488) (open, 2025-12)

- Raised by jbusecke at the earthaccess AGU hack day, motivated by
  [MUR-SST](https://github.com/developmentseed/mursst-icechunk-updater): NASA
  silently modifies files more than 9 days back with no metadata indication.
- Proposed `repo.readonly_session(..., disable_checksum_check=True)`.
- paraseba (maintainer) favoured a session-creation flag and added it to the
  Icechunk 2 project board. Not implemented as of 2.3.1.
- TomNicholas argued a dataset-specific problem should be fixed at write time.
- rabernat asked whether modified files even decode, i.e. whether chunk
  locations are preserved. For MUR-SST the reporter believes they are. For
  republished COGs this experiment shows they are not (`ignore-checksum` mode:
  zlib decode errors).

### [#2553 can't verify etag of an existing virtual chunk from http_store](https://github.com/earth-mover/icechunk/issues/2553) (open, 2026-11-13)

- Icechunk strips quotes from the stored ETag before sending `If-Match`, so
  RFC 9110-strict servers (Apache, e.g. NASA PPS) return 412, surfaced as
  `virtual-chunk-modified` for files that have not changed.
- A false-positive staleness signal. Under strict reads this fails every read
  touching that server; an argument for a tolerant policy even when nothing is
  actually stale.

### [#2484 Per-chunk checksums in `set_virtual_refs_arr`](https://github.com/earth-mover/icechunk/issues/2484) (open, 2026-10)

- maxrjones: each slice of a virtual array references a file written at a
  different time, so checksums should be per chunk, ideally ETags.
- Same need as `pin_etags` in `src/stale_cube/virtualize.py`. Maintainers
  supportive of widening the `checksum` type.

## VirtualiZarr

### [#436 Virtual chunk staleness check should be opt-out, not opt-in](https://github.com/zarr-developers/VirtualiZarr/issues/436) (closed, 2025-02)

- Origin of the `last_updated_at = now() + 1s` default in `to_icechunk`:
  validation was made on-by-default deliberately.

### [#843 Option to deactivate etag/checksum check in `.to_icechunk()`](https://github.com/zarr-developers/VirtualiZarr/issues/843) (open)

- Write-time opt-out spun off from icechunk#1488.
- maxrjones commented that a runtime (reader-side) setting in Icechunk would be
  better than not writing checksums.

### [#1100 last_updated_at should record parse-time or last-modified-time, not write-time](https://github.com/zarr-developers/VirtualiZarr/issues/1100) (open, 2026-09)

- The write-time default leaves a window where a file changes between parse and
  write, and Icechunk then serves wrong byte ranges with no error.
- Proposed fix: use ETags from obstore `GetResult.meta`, falling back to parse time.

## Not found

- No issue requesting a reader-side policy for failed validation (fill value
  plus a report of skipped chunks).
- Nothing relevant in zarr-python issues or Icechunk GitHub Discussions.

## Possible contribution

Comment on icechunk#1488 proposing a session-level policy, e.g.
`on_virtual_chunk_error="raise" | "fill"`, with skipped chunk keys reported to
the caller. Evidence: the matrix in `notebooks/stale_virtual_store.ipynb`.
Prototype: `TolerantStore` in `src/stale_cube/readers.py`.
