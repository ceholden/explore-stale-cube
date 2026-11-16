import numpy as np
import pytest

from stale_cube import data, readers
from stale_cube.virtualize import build_cube, open_repo

EXPECTED = np.array([data.expected_value(t) for t in range(data.N_TIMES)], dtype=float)
T_OVERWRITE, T_DELETE = 40, 70


def _scenario(prefix: str, checksum: str, change: str):
    urls = data.write_archive(prefix)
    build_cube(prefix, urls, checksum)
    if change == "overwrite":
        data.republish(prefix, T_OVERWRITE)
    else:
        data.delete(prefix, T_DELETE)
    return open_repo(prefix)


@pytest.fixture(scope="module")
def etag_overwrite():
    return _scenario("t_eo", "etag", "overwrite")


@pytest.fixture(scope="module")
def etag_delete():
    return _scenario("t_ed", "etag", "delete")


@pytest.fixture(scope="module")
def lastmod_overwrite():
    return _scenario("t_no", "lastmod", "overwrite")


def test_fresh_cube_reads_expected_series():
    prefix = "t_fresh"
    build_cube(prefix, data.write_archive(prefix), "etag")
    res = readers.read_strict(open_repo(prefix))
    assert res.error is None
    np.testing.assert_array_equal(res.values, EXPECTED)


def test_strict_fails_whole_series_on_one_republished_scene(etag_overwrite):
    res = readers.read_strict(etag_overwrite)
    assert res.values is None
    assert res.error_kind == "virtual-chunk-modified"


def test_tolerant_masks_only_the_stale_step(etag_overwrite):
    res = readers.read_tolerant(etag_overwrite)
    assert res.skipped == {T_OVERWRITE: "modified"}
    assert np.isnan(res.values[T_OVERWRITE])
    assert res.mask.sum() == 1 and res.mask[T_OVERWRITE]
    keep = ~res.mask
    np.testing.assert_array_equal(res.values[keep], EXPECTED[keep])


def test_tolerant_dask_window_read(etag_overwrite):
    sel = dict(y=slice(0, 512), x=slice(0, 512))
    res = readers.read_tolerant(etag_overwrite, sel=sel, chunks={})
    assert res.error is None
    assert np.isnan(res.values[T_OVERWRITE]).all()
    assert not np.isnan(np.delete(res.values, T_OVERWRITE, axis=0)).any()
    assert set(res.skipped) == {T_OVERWRITE}


def test_tolerant_masks_deleted_step(etag_delete):
    res = readers.read_tolerant(etag_delete)
    assert res.skipped == {T_DELETE: "missing"}
    assert np.isnan(res.values[T_DELETE])


def test_tolerant_reraises_non_staleness_errors(etag_overwrite):
    class Boom(Exception):
        kind = "throttled"

    assert readers.stale_reason(Boom("slow down")) is None


def test_check_then_repair_overwrite(etag_overwrite):
    assert readers.check(etag_overwrite, "t_eo") == {T_OVERWRITE: "modified"}
    readers.repair(etag_overwrite, "t_eo", {T_OVERWRITE: "modified"})
    res = readers.read_strict(etag_overwrite)
    assert res.error is None
    assert res.values[T_OVERWRITE] == data.expected_value(T_OVERWRITE, data.REPUBLISH_OFFSET)
    assert readers.check(etag_overwrite, "t_eo") == {}


def test_check_then_repair_delete(etag_delete):
    stale = readers.check(etag_delete, "t_ed")
    assert stale == {T_DELETE: "missing"}
    readers.repair(etag_delete, "t_ed", stale)
    res = readers.read_strict(etag_delete)
    assert res.error is None
    assert np.isnan(res.values[T_DELETE])


def test_lastmod_overwrite_is_not_caught_by_etag_check(lastmod_overwrite):
    assert readers.check(lastmod_overwrite, "t_no") == {}


def test_ignore_checksum_cannot_decode_shifted_republished_scene():
    prefix = "t_ic"
    build_cube(prefix, data.write_archive(prefix), "etag")
    data.republish(prefix, T_OVERWRITE)
    res = readers.read_ignore_checksum(open_repo(prefix), sel=dict(time=T_OVERWRITE))
    assert res.values is None
    assert "decompress" in res.error
