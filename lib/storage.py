"""Storage abstraction over local filesystem (dev) and Cloudflare R2 (prod).

All skills read and write snapshots through this module so they can run
identically in either environment. Backend is selected by the STORAGE env var.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Storage:
    def read_json(self, key: str) -> Any:
        raise NotImplementedError

    def write_json(self, key: str, value: Any) -> None:
        raise NotImplementedError

    def read_text(self, key: str) -> str:
        raise NotImplementedError

    def write_text(self, key: str, value: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def read_json(self, key: str) -> Any:
        with self._path(key).open() as f:
            return json.load(f)

    def write_json(self, key: str, value: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            json.dump(value, f, indent=2, sort_keys=False, default=str)

    def read_text(self, key: str) -> str:
        return self._path(key).read_text()

    def write_text(self, key: str, value: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class R2Storage(Storage):
    def __init__(self, bucket: str, endpoint: str, access_key: str, secret_key: str) -> None:
        import boto3  # imported lazily so local dev doesn't require boto3 to be importable

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

    def read_json(self, key: str) -> Any:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return json.loads(obj["Body"].read())

    def write_json(self, key: str, value: Any) -> None:
        body = json.dumps(value, indent=2, default=str).encode()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")

    def read_text(self, key: str) -> str:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read().decode()

    def write_text(self, key: str, value: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=value.encode(), ContentType="text/plain")

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False


def get_storage() -> Storage:
    backend = os.getenv("STORAGE", "local").lower()
    if backend == "r2":
        return R2Storage(
            bucket=_require("R2_BUCKET"),
            endpoint=_require("R2_ENDPOINT"),
            access_key=_require("R2_ACCESS_KEY"),
            secret_key=_require("R2_SECRET_KEY"),
        )
    return LocalStorage(Path("snapshots"))


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value
