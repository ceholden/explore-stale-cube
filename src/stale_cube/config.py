"""Connection settings for the local moto S3 server started by docker compose."""

import os

import boto3
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import S3Store

ENDPOINT = os.environ.get("STALE_CUBE_ENDPOINT", "http://localhost:5000")
BUCKET = "stale-cube"
REGION = "us-east-1"
ACCESS_KEY = "testing"
SECRET_KEY = "testing"


def boto3_client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def obstore_store() -> S3Store:
    return S3Store(
        BUCKET,
        endpoint=ENDPOINT,
        region=REGION,
        access_key_id=ACCESS_KEY,
        secret_access_key=SECRET_KEY,
        virtual_hosted_style_request=False,
        client_options={"allow_http": True},
    )


def obstore_registry() -> ObjectStoreRegistry:
    return ObjectStoreRegistry({f"s3://{BUCKET}": obstore_store()})


def reset_bucket() -> None:
    """Delete and recreate the bucket so every run starts from an empty archive."""
    s3 = boto3_client()
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys})
        s3.delete_bucket(Bucket=BUCKET)
    except s3.exceptions.NoSuchBucket:
        pass
    s3.create_bucket(Bucket=BUCKET)
