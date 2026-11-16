import rasterio
from rasterio.io import MemoryFile

from stale_cube import data
from stale_cube.config import BUCKET, boto3_client


def test_cog_layout_and_pixel():
    with MemoryFile(data.cog_bytes(7)) as mem, mem.open() as ds:
        assert ds.shape == (1024, 1024)
        assert ds.block_shapes == [(256, 256)]
        assert ds.compression == rasterio.enums.Compression.deflate
        assert ds.read(1)[5, 5] == data.expected_value(7)


def test_republish_changes_etag_and_delete_removes():
    data.write_cog("t_data", 3)
    before = data.etag("t_data", 3)
    data.republish("t_data", 3)
    assert data.etag("t_data", 3) != before
    data.delete("t_data", 3)
    resp = boto3_client().list_objects_v2(Bucket=BUCKET, Prefix="t_data/")
    assert resp.get("KeyCount", 0) == 0
