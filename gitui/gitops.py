"""Local git operations via the `git` command line (async subprocesses).

Authentication for HTTPS GitHub remotes is supplied per-command through a
scoped credential helper that reads the token from an environment variable,
so the token is never written to `.git/config` or the global git config.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

_HELPER = '!f() { echo username=x-access-token; echo "password=$GITUI_TOKEN"; }; f'


def git_available() -> bool:
    return shutil.which("git") is not None


def git_env(token: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    if token:
        env["GITUI_TOKEN"] = token
    return env


def auth_args(token: str | None) -> list[str]:
    if not token:
        return []
    return [
        "-c", "credential.https://github.com.helper=",
        "-c", f"credential.https://github.com.helper={_HELPER}",
    ]


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def message(self) -> str:
        text = (self.err or self.out).strip()
        return text.splitlines()[-1] if text else ("ok" if self.ok else f"exit {self.code}")


async def run(args: list[str], cwd: str | Path | None = None, token: str | None = None) -> Result:
    proc = await asyncio.create_subprocess_exec(
        "git", *auth_args(token), *args,
        cwd=str(cwd) if cwd else None,
        env=git_env(token),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return Result(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


async def stream(
    args: list[str],
    cwd: str | Path | None,
    token: str | None,
    on_line: Callable[[str], None],
) -> int:
    """Run git, calling on_line for every progress/output line (\\r or \\n split)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *auth_args(token), *args,
        cwd=str(cwd) if cwd else None,
        env=git_env(token),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    buf = b""
    while True:
        chunk = await proc.stdout.read(512)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for p in parts:
            if p.strip():
                on_line(p.decode("utf-8", "replace"))
    if buf.strip():
        on_line(buf.decode("utf-8", "replace"))
    return await proc.wait()


# ---------------------------------------------------------------- queries
@dataclass
class FileChange:
    path: str
    index: str  # status letter in index (staged)
    worktree: str  # status letter in work tree (unstaged)
    orig: str = ""

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def unstaged(self) -> bool:
        return self.worktree != " "

    @property
    def untracked(self) -> bool:
        return self.index == "?"


@dataclass
class Status:
    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    files: list[FileChange] = field(default_factory=list)
    detached: bool = False


async def is_repo(path: str | Path) -> bool:
    if not Path(path).is_dir():
        return False
    r = await run(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return r.ok and r.out.strip() == "true"


async def toplevel(path: str | Path) -> str | None:
    r = await run(["rev-parse", "--show-toplevel"], cwd=path)
    return r.out.strip() if r.ok else None


async def status(path: str | Path) -> Status:
    r = await run(["status", "--porcelain=v1", "-b", "-z", "--untracked-files=all"], cwd=path)
    st = Status()
    if not r.ok:
        raise RuntimeError(r.message)
    entries = r.out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            m = re.search(r"\[(.*)\]$", head)
            if m:
                for part in m.group(1).split(","):
                    part = part.strip()
                    if part.startswith("ahead "):
                        st.ahead = int(part[6:])
                    elif part.startswith("behind "):
                        st.behind = int(part[7:])
                head = head[: m.start()].strip()
            if head.startswith("No commits yet on "):
                st.branch = head[len("No commits yet on "):]
            elif head.startswith("HEAD (no branch)"):
                st.branch, st.detached = "HEAD (detached)", True
            elif "..." in head:
                st.branch, st.upstream = head.split("...", 1)
            else:
                st.branch = head
            continue
        x, y, p = e[0], e[1], e[3:]
        fc = FileChange(p, x, y)
        if x in "RC":
            fc.orig = entries[i] if i < len(entries) else ""
            i += 1
        st.files.append(fc)
    return st


async def diff(path: str | Path, change: FileChange, staged: bool) -> str:
    if change.untracked:
        full = Path(path) / change.path
        try:
            if full.stat().st_size > 512_000:
                return f"(new file, {full.stat().st_size // 1024} KiB — too large to preview)"
            text = full.read_text("utf-8")
        except UnicodeDecodeError:
            return "(new binary file)"
        except OSError as exc:
            return f"(cannot read: {exc})"
        return "\n".join(["new file: " + change.path, "@@ new @@"] + ["+" + l for l in text.splitlines()])
    args = ["diff", "--no-color", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    args += ["--", change.path]
    r = await run(args, cwd=path)
    return r.out or "(no textual changes)"


async def stage(path: str | Path, files: list[str]) -> Result:
    return await run(["add", "--", *files], cwd=path)


async def stage_all(path: str | Path) -> Result:
    return await run(["add", "-A"], cwd=path)


async def unstage(path: str | Path, files: list[str]) -> Result:
    r = await run(["restore", "--staged", "--", *files], cwd=path)
    if not r.ok:  # repo without commits yet
        r = await run(["rm", "--cached", "-r", "-q", "--", *files], cwd=path)
    return r


async def discard(path: str | Path, change: FileChange) -> Result:
    if change.untracked:
        target = Path(path) / change.path
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return Result(0, "", "")
        except OSError as exc:
            return Result(1, "", str(exc))
    return await run(["checkout", "HEAD", "--", change.path], cwd=path)


async def ensure_identity(path: str | Path, name: str, email: str) -> None:
    r = await run(["config", "user.email"], cwd=path)
    if not r.out.strip():
        await run(["config", "user.email", email], cwd=path)
    r = await run(["config", "user.name"], cwd=path)
    if not r.out.strip():
        await run(["config", "user.name", name], cwd=path)


async def commit(path: str | Path, message: str, amend: bool = False) -> Result:
    args = ["commit", "-m", message]
    if amend:
        args.append("--amend")
    return await run(args, cwd=path)


@dataclass
class Commit:
    sha: str
    short: str
    author: str
    date: str
    subject: str
    refs: str


async def log(path: str | Path, limit: int = 300) -> list[Commit]:
    fmt = "%H%x1f%h%x1f%an%x1f%ar%x1f%s%x1f%D%x1e"
    r = await run(["log", f"-n{limit}", f"--pretty=format:{fmt}"], cwd=path)
    commits = []
    for rec in r.out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) == 6:
            commits.append(Commit(*parts))
    return commits


async def show(path: str | Path, sha: str) -> str:
    r = await run(["show", "--no-color", "--stat", "-p", sha], cwd=path)
    return r.out


@dataclass
class Branch:
    name: str
    current: bool
    upstream: str
    track: str
    remote: bool = False


async def branches(path: str | Path) -> list[Branch]:
    fmt = "%(refname)%1f%(HEAD)%1f%(upstream:short)%1f%(upstream:track)"
    r = await run(["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes"], cwd=path)
    out = []
    for line in r.out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        ref, head, up, track = parts
        if ref.endswith("/HEAD"):
            continue
        if ref.startswith("refs/heads/"):
            out.append(Branch(ref[11:], head == "*", up, track))
        elif ref.startswith("refs/remotes/"):
            out.append(Branch(ref[13:], False, "", "", remote=True))
    return out


async def checkout(path: str | Path, branch: Branch) -> Result:
    if branch.remote:
        local = branch.name.split("/", 1)[1]
        r = await run(["switch", local], cwd=path)
        if r.ok:
            return r
        return await run(["switch", "-c", local, "--track", branch.name], cwd=path)
    return await run(["switch", branch.name], cwd=path)


async def new_branch(path: str | Path, name: str) -> Result:
    return await run(["switch", "-c", name], cwd=path)


async def delete_branch(path: str | Path, name: str) -> Result:
    return await run(["branch", "-d", name], cwd=path)


async def remote_url(path: str | Path) -> str:
    r = await run(["remote", "get-url", "origin"], cwd=path)
    return r.out.strip() if r.ok else ""


def github_full_name(url: str) -> str | None:
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


async def find_local_repos(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and (child / ".git").exists():
            found.append(child)
    return found


async def stash(path: str | Path, pop: bool = False) -> Result:
    return await run(["stash", "pop"] if pop else ["stash", "push", "-u"], cwd=path)
