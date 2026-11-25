"""Local working-tree view: stage, diff, commit, push, pull, branches, log."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane

from .. import gitops
from ..ui import diff_text
from .dialogs import ConfirmDialog, GitRunDialog, HelpDialog, InputDialog, LinkDialog, TextDialog

TABS = ["changes", "log", "branches"]

STATUS_NAMES = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflict", "?": "new", "T": "type"}

HELP = [
    ("Local repository", [
        ("1 2 3", "Changes / Log / Branches"),
        ("← →  h l", "Previous / next tab"),
        ("↑ ↓  j k", "Move"),
        ("space / enter", "Stage ↔ unstage file (Branches: checkout)"),
        ("a / A", "Stage all / unstage all"),
        ("d", "Discard changes to file"),
        ("c", "Commit staged changes"),
        ("p", "Push (sets upstream automatically)"),
        ("P", "Pull"),
        ("f", "Fetch all remotes"),
        ("b", "Create & switch to new branch"),
        ("D", "Delete branch (Branches tab)"),
        ("z / Z", "Stash / pop stash"),
        ("w", "Show GitHub link + QR"),
        ("r", "Refresh"),
        ("esc", "Back"),
    ]),
]


class LocalRepoScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("1", "tab(0)", show=False),
        Binding("2", "tab(1)", show=False),
        Binding("3", "tab(2)", show=False),
        Binding("left,h", "step(-1)", show=False),
        Binding("right,l", "step(1)", show=False),
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("space", "toggle", "Stage/Unstage"),
        Binding("a", "stage_all", "Stage all"),
        Binding("A", "unstage_all", "Unstage all", show=False),
        Binding("d", "discard", "Discard", show=False),
        Binding("c", "commit", "Commit"),
        Binding("p", "push", "Push"),
        Binding("P", "pull", "Pull"),
        Binding("f", "fetch", "Fetch"),
        Binding("b", "new_branch", "Branch"),
        Binding("D", "delete_branch", "Del branch", show=False),
        Binding("z", "stash", "Stash", show=False),
        Binding("Z", "stash_pop", "Pop stash", show=False),
        Binding("w", "web", "Link", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, path: str):
        super().__init__()
        self.path = str(Path(path).resolve())
        self.status = gitops.Status()
        self.file_rows: list[tuple[gitops.FileChange, bool]] = []
        self.commits: list[gitops.Commit] = []
        self.branch_list: list[gitops.Branch] = []
        self.full_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with TabbedContent(initial="changes"):
            with TabPane("1 Changes", id="changes"):
                with Horizontal(classes="split"):
                    yield DataTable(id="files", cursor_type="row", classes="left")
                    with VerticalScroll(id="diff-scroll", classes="right"):
                        yield Static(id="diff")
            with TabPane("2 Log", id="log"):
                with Horizontal(classes="split"):
                    yield DataTable(id="commits", cursor_type="row", classes="left")
                    with VerticalScroll(id="show-scroll", classes="right"):
                        yield Static(id="show")
            with TabPane("3 Branches", id="branches"):
                yield DataTable(id="branch-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#files", DataTable).focus()
        self.refresh_all()

    # ------------------------------------------------------------ helpers
    @property
    def active(self) -> str:
        return self.query_one(TabbedContent).active or "changes"

    def action_tab(self, idx: int) -> None:
        self.query_one(TabbedContent).active = TABS[idx]

    def action_step(self, delta: int) -> None:
        self.action_tab((TABS.index(self.active) + delta) % len(TABS))

    @on(TabbedContent.TabActivated)
    def tab_activated(self) -> None:
        target = {"changes": "#files", "log": "#commits", "branches": "#branch-table"}[self.active]
        self.query_one(target, DataTable).focus()
        if self.active == "log":
            self.load_log()
        elif self.active == "branches":
            self.load_branches()

    def action_move(self, delta: int) -> None:
        w = self.focused
        if isinstance(w, DataTable):
            (w.action_cursor_down if delta > 0 else w.action_cursor_up)()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self.refresh_all()

    def render_top(self) -> None:
        st = self.status
        t = Text.assemble((" ◆ gitui ", "bold reverse"), ("  ", ""), (Path(self.path).name, "bold"), ("  ⎇ ", "#22D3EE"), (st.branch or "?", "bold #22D3EE"))
        if st.upstream:
            t.append(f"  → {st.upstream}", style="dim")
        else:
            t.append("  (no upstream)", style="dim italic")
        if st.ahead:
            t.append(f"  ↑{st.ahead}", style="bold #34D399")
        if st.behind:
            t.append(f"  ↓{st.behind}", style="bold #FBBF24")
        t.append(f"   {self.path}", style="dim")
        self.query_one("#topbar", Static).update(t)
        staged = sum(1 for f in st.files if f.staged)
        unstaged = sum(1 for f in st.files if f.unstaged)
        msg = f" {staged} staged · {unstaged} unstaged"
        if not st.files:
            msg = " ✔ Working tree clean"
            if st.ahead:
                msg += f" — {st.ahead} commit(s) to push (p)"
        self.query_one("#statusbar", Static).update(msg)

    # ------------------------------------------------------------ changes
    @work(group="status", exclusive=True)
    async def refresh_all(self) -> None:
        if not await gitops.is_repo(self.path):
            self.notify("Not a git repository.", severity="error")
            self.dismiss(None)
            return
        if self.full_name is None:
            self.full_name = gitops.github_full_name(await gitops.remote_url(self.path)) or ""
        try:
            self.status = await gitops.status(self.path)
        except RuntimeError as exc:
            self.notify(str(exc), severity="error")
            return
        self.render_top()
        self.fill_files()
        if self.active == "log":
            self.load_log()
        elif self.active == "branches":
            self.load_branches()

    def fill_files(self) -> None:
        table = self.query_one("#files", DataTable)
        prev = table.cursor_row or 0
        table.clear(columns=True)
        table.add_columns("", "State", "File")
        self.file_rows = []
        staged = [f for f in self.status.files if f.staged]
        unstaged = [f for f in self.status.files if f.unstaged]
        for f in staged:
            self.file_rows.append((f, True))
            table.add_row(Text("●", style="bold #34D399"), Text(STATUS_NAMES.get(f.index, f.index), style="#34D399"), Text(f.path, style="bold"))
        for f in unstaged:
            self.file_rows.append((f, False))
            letter = "?" if f.untracked else f.worktree
            color = "#22D3EE" if f.untracked else "#FBBF24"
            table.add_row(Text("○", style=color), Text(STATUS_NAMES.get(letter, letter), style=color), Text(f.path))
        if not self.file_rows:
            table.add_row("", "", Text("Nothing to commit — working tree clean ✨", style="dim italic"))
            self.query_one("#diff", Static).update(Text("No changes.", style="dim"))
        else:
            table.move_cursor(row=min(prev, len(self.file_rows) - 1))
            self.show_diff()

    def selected_file(self) -> tuple[gitops.FileChange, bool] | None:
        table = self.query_one("#files", DataTable)
        if not self.file_rows or table.cursor_row is None or table.cursor_row >= len(self.file_rows):
            return None
        return self.file_rows[table.cursor_row]

    @on(DataTable.RowHighlighted, "#files")
    def file_highlighted(self) -> None:
        self.show_diff()

    @work(group="diff", exclusive=True)
    async def show_diff(self) -> None:
        sel = self.selected_file()
        if not sel:
            return
        f, staged = sel
        text = await gitops.diff(self.path, f, staged)
        header = Text()
        header.append(" STAGED " if staged else " UNSTAGED ", style="bold reverse")
        header.append(f"  {f.path}\n\n", style="bold")
        header.append_text(diff_text(text[:200_000]))
        self.query_one("#diff", Static).update(header)
        self.query_one("#diff-scroll", VerticalScroll).scroll_home(animate=False)

    @on(DataTable.RowSelected, "#files")
    def file_selected(self) -> None:
        self.action_toggle()

    @work
    async def action_toggle(self) -> None:
        if self.active == "branches":
            await self.checkout_selected()
            return
        if self.active != "changes":
            return
        sel = self.selected_file()
        if not sel:
            return
        f, staged = sel
        r = await (gitops.unstage(self.path, [f.path]) if staged else gitops.stage(self.path, [f.path]))
        if not r.ok:
            self.notify(r.message, severity="error")
        self.refresh_all()

    @work
    async def action_stage_all(self) -> None:
        r = await gitops.stage_all(self.path)
        if not r.ok:
            self.notify(r.message, severity="error")
        self.refresh_all()

    @work
    async def action_unstage_all(self) -> None:
        files = [f.path for f in self.status.files if f.staged]
        if files:
            r = await gitops.unstage(self.path, files)
            if not r.ok:
                self.notify(r.message, severity="error")
        self.refresh_all()

    @work
    async def action_discard(self) -> None:
        sel = self.selected_file()
        if not sel or self.active != "changes":
            return
        f, _ = sel
        what = "delete this untracked file" if f.untracked else "throw away all changes to this file"
        if not await self.app.push_screen_wait(ConfirmDialog("Discard changes", f"Really {what}?\n[b]{f.path}[/b]", danger=True)):
            return
        r = await gitops.discard(self.path, f)
        self.notify("Discarded" if r.ok else r.message, severity="information" if r.ok else "error")
        self.refresh_all()

    @work
    async def action_commit(self) -> None:
        if not any(f.staged for f in self.status.files):
            if not self.status.files:
                self.notify("Nothing to commit.", severity="warning")
                return
            if not await self.app.push_screen_wait(ConfirmDialog("Nothing staged", "Stage [b]all[/b] changes and commit?")):
                return
            await gitops.stage_all(self.path)
        msg = await self.app.push_screen_wait(TextDialog("✎ Commit", with_title=False, body_label="Commit message (first line = summary)"))
        if not msg:
            self.refresh_all()
            return
        user = self.app.user or {}
        login = user.get("login") or self.app.config.login
        await gitops.ensure_identity(
            self.path,
            user.get("name") or login,
            f"{user.get('id', 0)}+{login}@users.noreply.github.com",
        )
        r = await gitops.commit(self.path, msg.strip())
        if r.ok:
            self.notify(f"Committed: {msg.strip().splitlines()[0]}  — press p to push")
        else:
            self.notify(r.message, severity="error", timeout=8)
        self.refresh_all()

    # ------------------------------------------------------------ remote ops
    async def run_git(self, title: str, args: list[str]) -> bool:
        ok = await self.app.push_screen_wait(GitRunDialog(title, args, self.path, self.app.config.token))
        self.refresh_all()
        return bool(ok)

    @work
    async def action_push(self) -> None:
        if self.status.detached:
            self.notify("Detached HEAD — create a branch first (b).", severity="warning")
            return
        args = ["push", "--progress"]
        if not self.status.upstream:
            args += ["-u", "origin", "HEAD"]
        await self.run_git("⇡ Push", args)

    @work
    async def action_pull(self) -> None:
        await self.run_git("⇣ Pull", ["pull", "--no-rebase", "--progress"])

    @work
    async def action_fetch(self) -> None:
        await self.run_git("⟳ Fetch", ["fetch", "--all", "--prune", "--progress"])

    @work
    async def action_stash(self) -> None:
        r = await gitops.stash(self.path)
        self.notify("Stashed changes" if r.ok else r.message, severity="information" if r.ok else "error")
        self.refresh_all()

    @work
    async def action_stash_pop(self) -> None:
        r = await gitops.stash(self.path, pop=True)
        self.notify("Popped stash" if r.ok else r.message, severity="information" if r.ok else "error")
        self.refresh_all()

    # ------------------------------------------------------------ log
    @work(group="log", exclusive=True)
    async def load_log(self) -> None:
        self.commits = await gitops.log(self.path)
        table = self.query_one("#commits", DataTable)
        table.clear(columns=True)
        table.add_columns("SHA", "Message", "Author", "When")
        for c in self.commits:
            msg = Text()
            if c.refs:
                for ref in c.refs.split(", "):
                    ref = ref.replace("HEAD -> ", "▶ ")
                    msg.append(f" {ref} ", style="bold #000000 on #22D3EE" if "origin/" not in ref else "bold #000000 on #A78BFA")
                    msg.append(" ")
            msg.append(c.subject)
            table.add_row(Text(c.short, style="#FBBF24"), msg, Text(c.author, style="#22D3EE"), Text(c.date, style="dim"))
        if not self.commits:
            table.add_row("", Text("No commits yet", style="dim italic"), "", "")
        else:
            self.show_commit()

    @on(DataTable.RowHighlighted, "#commits")
    def commit_highlighted(self) -> None:
        self.show_commit()

    @work(group="show", exclusive=True)
    async def show_commit(self) -> None:
        table = self.query_one("#commits", DataTable)
        if not self.commits or table.cursor_row is None or table.cursor_row >= len(self.commits):
            return
        text = await gitops.show(self.path, self.commits[table.cursor_row].sha)
        self.query_one("#show", Static).update(diff_text(text[:200_000]))
        self.query_one("#show-scroll", VerticalScroll).scroll_home(animate=False)

    # ------------------------------------------------------------ branches
    @work(group="branches", exclusive=True)
    async def load_branches(self) -> None:
        self.branch_list = await gitops.branches(self.path)
        self.branch_list.sort(key=lambda b: (not b.current, b.remote, b.name))
        table = self.query_one("#branch-table", DataTable)
        table.clear(columns=True)
        table.add_columns("", "Branch", "Upstream", "Sync")
        for b in self.branch_list:
            table.add_row(
                Text("▶" if b.current else ("☁" if b.remote else "⎇"), style="bold #34D399" if b.current else "dim"),
                Text(b.name, style="bold" if b.current else ("dim" if b.remote else "")),
                Text(b.upstream, style="dim"),
                Text(b.track, style="#FBBF24"),
            )
        if not self.branch_list:
            table.add_row("", Text("No branches yet (make a first commit)", style="dim italic"), "", "")

    @on(DataTable.RowSelected, "#branch-table")
    def branch_selected(self) -> None:
        self.action_toggle()

    async def checkout_selected(self) -> None:
        table = self.query_one("#branch-table", DataTable)
        if not self.branch_list or table.cursor_row is None or table.cursor_row >= len(self.branch_list):
            return
        b = self.branch_list[table.cursor_row]
        if b.current:
            return
        r = await gitops.checkout(self.path, b)
        self.notify(f"Switched to {b.name}" if r.ok else r.message, severity="information" if r.ok else "error", timeout=6)
        self.refresh_all()

    @work
    async def action_new_branch(self) -> None:
        name = await self.app.push_screen_wait(InputDialog("⎇ New branch", "Create and switch to:", placeholder="feature/awesome"))
        if not name:
            return
        r = await gitops.new_branch(self.path, name.strip())
        self.notify(f"Now on {name}" if r.ok else r.message, severity="information" if r.ok else "error")
        self.refresh_all()

    @work
    async def action_delete_branch(self) -> None:
        if self.active != "branches":
            return
        table = self.query_one("#branch-table", DataTable)
        if not self.branch_list or table.cursor_row is None or table.cursor_row >= len(self.branch_list):
            return
        b = self.branch_list[table.cursor_row]
        if b.current or b.remote:
            self.notify("Can't delete the current or a remote branch here.", severity="warning")
            return
        if not await self.app.push_screen_wait(ConfirmDialog("Delete branch", f"Delete local branch [b]{b.name}[/b]?", danger=True)):
            return
        r = await gitops.delete_branch(self.path, b.name)
        self.notify(f"Deleted {b.name}" if r.ok else r.message, severity="information" if r.ok else "error", timeout=8)
        self.load_branches()

    def action_web(self) -> None:
        if self.full_name:
            self.app.push_screen(LinkDialog(self.full_name, f"https://github.com/{self.full_name}"))
        else:
            self.notify("No GitHub remote.", severity="warning")

    def action_help(self) -> None:
        self.app.push_screen(HelpDialog(HELP))
