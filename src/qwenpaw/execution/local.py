"""Local execution target used when no plugin supplies another target."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Mapping

from ..config.context import get_tool_base_dir
from ..utils.io_utils import append_text_async, write_text_atomic_async
from .base import ExecutionResult, ExecutionTargetSnapshot, TargetHealth


LOCAL_CAPABILITIES = frozenset(
    {
        "fs.read",
        "fs.write",
        "fs.search",
        "proc.exec",
        "file.transfer.local",
        "media.local",
        "ast.local",
        "screen.local",
        "browser.local",
        "host.cli",
    },
)


class LocalExecutionTarget:
    """The host-backed target preserving QwenPaw's current local mode."""

    is_local = True

    @property
    def snapshot(self) -> ExecutionTargetSnapshot:
        return ExecutionTargetSnapshot(
            target_id="local",
            generation="local",
            capabilities=LOCAL_CAPABILITIES,
            metadata={"kind": "local"},
        )

    def resolve(self, path: str, *, base: str | None = None) -> str:
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return str(candidate)
        root = Path(base).expanduser() if base else get_tool_base_dir()
        return str(root / path)

    async def health(self) -> TargetHealth:
        return TargetHealth()

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return await asyncio.to_thread(Path(path).read_text, encoding=encoding)

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = True,
    ) -> None:
        if atomic:
            await write_text_atomic_async(
                path,
                content,
                encoding=encoding,
                new_file_mode=0o644,
            )
        else:
            await asyncio.to_thread(Path(path).write_text, content, encoding=encoding)

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None:
        await append_text_async(path, content, encoding=encoding)

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or b"",
            stderr=completed.stderr or b"",
        )

    async def grep(self, pattern: str, **_kwargs) -> str:
        raise NotImplementedError(
            "LocalExecutionTarget grep is provided by the existing local tool",
        )

    async def glob(self, pattern: str, **_kwargs) -> list[str]:
        raise NotImplementedError(
            "LocalExecutionTarget glob is provided by the existing local tool",
        )


__all__ = ["LOCAL_CAPABILITIES", "LocalExecutionTarget"]
