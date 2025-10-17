# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse


class LocalBlobServiceClient:
    """Minimal BlobServiceClient-like API backed by the local filesystem."""

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.account_name = "local"

    def get_container_client(self, container_name: str) -> "LocalContainerClient":
        return LocalContainerClient(self.base_path / container_name)


class LocalContainerClient:
    """Container client that reads and writes blobs under a directory."""

    def __init__(self, container_path: Path):
        self.container_path = container_path.resolve()
        self.container_path.mkdir(parents=True, exist_ok=True)

    def get_blob_client(self, blob_path: str) -> "LocalBlobClient":
        return LocalBlobClient(self.container_path / blob_path)

    async def list_blob_names(self, name_starts_with: Optional[str] = None) -> AsyncGenerator[str, None]:
        prefix = name_starts_with or ""
        for file_path in sorted(self.container_path.rglob("*")):
            if file_path.is_file():
                relative = file_path.relative_to(self.container_path).as_posix()
                if relative.startswith(prefix):
                    yield relative

    async def download_blob(self, blob_path: str) -> "LocalBlobStream":
        return await self.get_blob_client(blob_path).download_blob()

    async def upload_blob(self, blob_path: str, data, overwrite: bool = True) -> None:
        await self.get_blob_client(blob_path).upload_blob(data, overwrite=overwrite)

    async def delete_blob(self, blob_path: str) -> None:
        await self.get_blob_client(blob_path).delete_blob()


class LocalBlobClient:
    """Blob client that persists content on disk."""

    def __init__(self, blob_path: Path):
        self.blob_path = blob_path

    @property
    def url(self) -> str:
        return self.blob_path.resolve().as_uri()

    async def download_blob(self) -> "LocalBlobStream":
        return LocalBlobStream(self.blob_path)

    async def upload_blob(self, data, overwrite: bool = True) -> None:
        if self.blob_path.exists() and not overwrite:
            raise FileExistsError(f"Blob {self.blob_path} already exists.")

        self.blob_path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, (bytes, bytearray, memoryview)):
            content: bytes = bytes(data)
        elif isinstance(data, str):
            content = data.encode("utf-8")
        elif hasattr(data, "read"):
            raw = data.read()
            if asyncio.iscoroutine(raw):
                raw = await raw
            content = raw.encode("utf-8") if isinstance(raw, str) else raw
        else:
            raise TypeError(f"Unsupported payload type {type(data)!r} for upload_blob.")

        await asyncio.to_thread(self.blob_path.write_bytes, content)

    async def delete_blob(self) -> None:
        try:
            await asyncio.to_thread(self.blob_path.unlink)
        except FileNotFoundError:
            pass

    async def start_copy_from_url(self, source_url: str, requires_sync: bool = True) -> None:
        parsed = urlparse(source_url)
        if parsed.scheme == "file":
            source_path = Path(parsed.path)
        else:
            source_path = Path(source_url)
        data = await asyncio.to_thread(source_path.read_bytes)
        await self.upload_blob(data, overwrite=True)


@dataclass
class LocalBlobStream:
    """Stream wrapper returned by download_blob to mimic Azure SDK semantics."""

    blob_path: Path

    async def readall(self) -> bytes:
        return await asyncio.to_thread(self.blob_path.read_bytes)

    async def readinto(self, stream) -> int:
        data = await self.readall()
        stream.write(data)
        return len(data)
