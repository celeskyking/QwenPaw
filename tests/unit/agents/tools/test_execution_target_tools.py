from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from qwenpaw.agents.tools.file_io import (
    append_file,
    edit_file,
    read_file,
    write_file,
)
from qwenpaw.agents.tools.file_search import glob_search, grep_search
from qwenpaw.agents.tools.shell import execute_shell_command
from qwenpaw.execution import (
    ExecutionResult,
    ExecutionTargetSnapshot,
    TargetHealth,
    reset_execution_target,
    set_execution_target,
)


@dataclass
class FakeRemoteTarget:
    calls: list[tuple] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=lambda: {"/remote/a.txt": "old"})
    is_local: bool = False

    @property
    def snapshot(self):
        return ExecutionTargetSnapshot(
            target_id="fake-remote",
            generation="g1",
            capabilities=frozenset({"fs.read", "fs.write", "fs.search", "proc.exec"}),
        )

    def resolve(self, path: str, *, base: str | None = None) -> str:
        self.calls.append(("resolve", path, base))
        if path.startswith("/"):
            return path
        return f"/remote/{path}"

    async def health(self):
        return TargetHealth()

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self.calls.append(("read", path, encoding))
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = True,
    ) -> None:
        self.calls.append(("write", path, content, encoding, atomic))
        self.files[path] = content

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None:
        self.calls.append(("append", path, content, encoding))
        self.files[path] = self.files.get(path, "") + content

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env=None,
        timeout: float | None = None,
    ):
        self.calls.append(("execute", command, cwd, timeout))
        return ExecutionResult(0, b"remote output\n", b"")

    async def grep(self, pattern: str, **kwargs) -> str:
        self.calls.append(("grep", pattern, kwargs))
        return "/remote/a.txt:1:> old"

    async def glob(self, pattern: str, **kwargs) -> list[str]:
        self.calls.append(("glob", pattern, kwargs))
        return ["/remote/a.txt"]


@pytest.fixture
def remote_target():
    target = FakeRemoteTarget()
    token = set_execution_target(target)
    try:
        yield target
    finally:
        reset_execution_target(token)


@pytest.mark.asyncio
async def test_file_tools_use_remote_target(remote_target):
    result = await read_file("a.txt")
    assert "old" in result.content[0].text

    await write_file("a.txt", "new")
    await edit_file("a.txt", "new", "edited")
    await append_file("a.txt", "!")

    assert remote_target.files["/remote/a.txt"] == "edited!"
    assert [call[0] for call in remote_target.calls] == [
        "resolve",
        "read",
        "resolve",
        "write",
        "resolve",
        "read",
        "write",
        "resolve",
        "append",
    ]


@pytest.mark.asyncio
async def test_search_tools_resolve_remote_paths(remote_target):
    grep_result = await grep_search("old", path="src")
    glob_result = await glob_search("**/*.py", path="src")

    assert "/remote/a.txt" in grep_result.content[0].text
    assert "/remote/a.txt" in glob_result.content[0].text
    assert (
        "grep",
        "old",
        {
            "path": "/remote/src",
            "is_regex": False,
            "case_sensitive": True,
            "context_lines": 0,
            "include_pattern": None,
            "show_file": True,
        },
    ) in remote_target.calls
    assert ("glob", "**/*.py", {"path": "/remote/src"}) in remote_target.calls


@pytest.mark.asyncio
async def test_shell_tool_uses_remote_target(remote_target):
    result = await execute_shell_command("pwd", timeout=17.0, cwd="work")

    assert "remote output" in result.content[0].text
    execute_calls = [call for call in remote_target.calls if call[0] == "execute"]
    assert execute_calls == [("execute", "pwd", "/remote/work", 17.0)]
