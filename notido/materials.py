import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path

from .errors import NotiDoError
from .keys import uid
from .models import MaterialBudget


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        self.active_staging = set()
        self.active_derived = set()
        for folder in ("staging", "blobs", "derived"):
            (root / folder).mkdir(parents=True, exist_ok=True)

    async def save(self, stream, *, max_bytes=20 * 1024**2, min_free_bytes=1024**3):
        if shutil.disk_usage(self.root).free < min_free_bytes:
            raise NotiDoError("STORAGE_FULL", "存储不足，暂未接收原件，请稍后重发。", status=429)
        staging = self.root / "staging" / uid()
        self.active_staging.add(staging.resolve())
        digest, size = hashlib.sha256(), 0
        try:
            with staging.open("xb") as out:
                while chunk := await stream.read(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise NotiDoError(
                            "FILE_TOO_LARGE", "原件超过配置字节预算，请提供较小材料。", status=400
                        )
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            hash_value = digest.hexdigest()
            destination = self.root / "blobs" / hash_value
            # Content-addressed blobs are immutable. Never overwrite an existing blob.
            try:
                os.link(staging, destination)
            except FileExistsError:
                if await asyncio.to_thread(self.hash_file, destination) != hash_value:
                    raise NotiDoError(
                        "BLOB_CORRUPT", "原件存储校验失败，进入维护状态。", status=503
                    ) from None
            return {"hash": hash_value, "size": size, "path": f"blobs/{hash_value}"}
        finally:
            staging.unlink(missing_ok=True)
            self.active_staging.discard(staging.resolve())

    @staticmethod
    def hash_file(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as file:
            while chunk := file.read(65536):
                digest.update(chunk)
        return digest.hexdigest()

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to((self.root / "blobs").resolve()) or not path.is_file():
            raise NotiDoError("ASSET_UNAVAILABLE", "原件不可用，请补发。", status=404)
        return path

    async def read(self, path, name, *, budget=None, deadline_seconds=60):
        output = self.root / "derived" / uid()
        output.mkdir()
        self.active_derived.add(output.resolve())
        try:
            return await self._read(
                path, name, output, budget or MaterialBudget(), deadline_seconds
            )
        finally:
            self.active_derived.discard(output.resolve())

    async def _read(self, path, name, output, budget, deadline_seconds):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("read_worker.py")),
            str(path),
            Path(name).suffix.lower(),
            str(output),
            budget.model_dump_json(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            **({"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}),
        )

        async def terminate():
            if process.returncode is not None:
                return
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()

        async def collect():
            chunks, size = [], 0
            while chunk := await process.stdout.read(65536):
                size += len(chunk)
                if size > 2 * 1024**2:
                    raise ValueError("reader output limit")
                chunks.append(chunk)
            await process.wait()
            return b"".join(chunks)

        try:
            stdout = await asyncio.wait_for(collect(), deadline_seconds)
        except (TimeoutError, ValueError):
            await terminate()
            return {"segments": [], "unknowns": ["READ_TIMEOUT"], "visuals": []}
        except asyncio.CancelledError:
            await terminate()
            raise
        if len(stdout) > 2 * 1024**2 or process.returncode:
            return {"segments": [], "unknowns": ["READ_FAILED"], "visuals": []}
        try:
            return json.loads(stdout)
        except ValueError:
            return {"segments": [], "unknowns": ["READ_FAILED"], "visuals": []}


class AsyncFile:
    def __init__(self, file):
        self.file = file

    async def read(self, size):
        return await asyncio.to_thread(self.file.read, size)
