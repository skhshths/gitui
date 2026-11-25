"""Main dashboard: your repositories, starred, local clones and search."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from .. import gitops
from ..github import GitHubError
from ..ui import ago, compact, lang_text
from .dialogs import (
    ConfirmDialog,
    GitRunDialog,
    HelpDialog,
    InputDialog,
    LinkDialog,
    NewRepoDialog,
    TypeToConfirmDialog,
)

SECTIONS = [("repos", "Repositories"), ("starred", "Starred"), ("local", "Local"), ("explore", "Explore")]
SORTS = ["updated", "name", "stars"]

HELP = [
    ("Navigation", [
        ("1 2 3 4", "Repositories / Starred / Local / Explore"),
        ("tab", "Next section"),
        ("↑ ↓  j k", "Move"),
        ("g / G", "Top / bottom"),
        ("enter", "Open repository"),
        ("/", "Filter (Explore: search GitHub)"),
        ("esc", "Clear filter"),
        ("s", "Cycle sort: updated / name / stars"),
    ]),
    ("Actions", [
        ("c", "Clone selected repository"),
        ("o", "Open local clone (status, commit, push…)"),
        ("O", "Open any local folder by path"),
        ("n", "Create a new repository"),
        ("*", "Star / unstar"),
        ("f", "Fork"),
        ("D", "Delete repository (type name to confirm)"),
        ("w", "Show link + QR code"),
        ("y", "Copy clone URL"),
        ("r", "Refresh"),
    ]),
    ("App", [
        ("t", "Theme picker (live preview)"),
        (",", "Settings / sign out"),
        ("?", "This help"),
        ("q", "Quit"),
    ]),
]


class Dashboard(Screen):
    BINDINGS = [
        Binding("1", "section('repos')", "Repos", show=False),
        Binding("2", "section('starred')", "Starred", show=False),
        Binding("3", "section('local')", "Local", show=False),
        Binding("4", "section('explore')", "Explore", show=False),
        Binding("tab", "next_section", "Section", show=False),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear", show=False),
        Binding("c", "clone", "Clone"),
        Binding("o", "open_local", "Local"),
        Binding("O", "open_path", "Open path", show=False),
        Binding("n", "new_repo", "New"),
        Binding("asterisk", "star", "Star", show=False),
        Binding("f", "fork", "Fork", show=False),
        Binding("D", "delete", "Delete", show=False),
        Binding("w", "web", "Link", show=False),
        Binding("y", "copy_url", "Copy URL", show=False),
        Binding("s", "sort", "Sort", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
        Binding("g", "cursor('top')", show=False),
        Binding("G", "cursor('bottom')", show=False),
        Binding("t", "theme", "Theme"),
        Binding("comma", "settings", "Settings"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.section = "repos"
        self.sort = "updated"
        self.data: dict[str, list[dict]] = {k: [] for k, _ in SECTIONS}
        self.loaded: set[str] = set()
        self.filter_text = ""
        self.rows: list[dict] = []
        self.search_query = ""

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        yield Static(id="tabs")
        yield Input(placeholder="Filter…", id="filter", classes="hidden")
        with Horizontal(id="main"):
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="info"):
                yield Static(id="info-body")
        yield Static(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self.render_bars()
        self.query_one("#table", DataTable).focus()
        self.load(self.section)

    # ----------------------------------------------------------- rendering
    def render_bars(self) -> None:
        cfg = self.app.config
        top = Text.assemble(
            (" ◆ gitui ", "bold reverse"),
            ("  ", ""),
            (f"@{cfg.login}", "bold"),
            ("   ", ""),
            (f"clones → {cfg.clone_dir}", "dim"),
        )
        self.query_one("#topbar", Static).update(top)
        tabs = Text()
        for i, (key, label) in enumerate(SECTIONS, 1):
            if key == self.section:
                tabs.append(f" {i} {label} ", style="bold reverse")
            else:
                tabs.append(f" {i} ", style="bold")
                tabs.append(f"{label} ", style="dim")
            tabs.append("  ")
        tabs.append(f"   sort: {self.sort}", style="dim italic")
        if self.filter_text:
            tabs.append(f"   filter: “{self.filter_text}”", style="italic")
        self.query_one("#tabs", Static).update(tabs)

    def local_path(self, repo: dict) -> Path:
        return self.app.config.clone_path / repo["name"]

    def is_cloned(self, repo: dict) -> bool:
        return (self.local_path(repo) / ".git").exists()

    def filtered(self) -> list[dict]:
        items = list(self.data[self.section])
        f = self.filter_text.lower()
        if f and self.section != "explore":
            items = [
                r for r in items
                if f in (r.get("full_name") or r.get("name") or "").lower()
                or f in (r.get("description") or "").lower()
                or f in (r.get("language") or "").lower()
            ]
        if self.section == "local":
            return items
        if self.sort == "name":
            items.sort(key=lambda r: r["full_name"].lower())
        elif self.sort == "stars":
            items.sort(key=lambda r: -(r.get("stargazers_count") or 0))
        elif self.section != "explore":
            items.sort(key=lambda r: r.get("pushed_at") or r.get("updated_at") or "", reverse=True)
        return items

    def render_table(self) -> None:
        table = self.query_one("#table", DataTable)
        prev = self.current()
        prev_key = (prev or {}).get("full_name") or (prev or {}).get("path")
        table.clear(columns=True)
        self.rows = self.filtered()
        if self.section == "local":
            table.add_columns("", "Folder", "Branch", "State", "Remote")
            for r in self.rows:
                state = Text("● changes", style="bold #FBBF24") if r["dirty"] else Text("✔ clean", style="#34D399")
                sync = ""
                if r["ahead"]:
                    sync += f"↑{r['ahead']} "
                if r["behind"]:
                    sync += f"↓{r['behind']}"
                table.add_row(
                    Text("▣", style="bold"),
                    Text(r["name"], style="bold"),
                    Text(r["branch"] + (f"  {sync}" if sync else ""), style="#22D3EE"),
                    state,
                    Text(r["full_name"] or "—", style="dim"),
                    key=r["path"],
                )
        else:
            table.add_columns("", "Repository", "Language", "★", "Updated", "")
            for r in self.rows:
                vis = Text("🔒" if r.get("private") else "◉", style="#FBBF24" if r.get("private") else "#34D399")
                name = Text()
                owner, _, nm = r["full_name"].partition("/")
                if owner.lower() != self.app.config.login.lower():
                    name.append(owner + "/", style="dim")
                name.append(nm, style="bold")
                if r.get("fork"):
                    name.append("  ⑂", style="dim")
                if r.get("archived"):
                    name.append("  archived", style="dim italic")
                local = Text("✔ local", style="#22D3EE") if self.is_cloned(r) else Text("")
                table.add_row(
                    vis,
                    name,
                    lang_text(r.get("language")),
                    Text(compact(r.get("stargazers_count")), style="#FBBF24"),
                    Text(ago(r.get("pushed_at") or r.get("updated_at")), style="dim"),
                    local,
                    key=r["full_name"],
                )
        # restore cursor
        if prev_key:
            for i, r in enumerate(self.rows):
                if (r.get("full_name") or r.get("path")) == prev_key:
                    table.move_cursor(row=i)
                    break
        self.update_status()
        self.update_info()

    def update_status(self, extra: str = "") -> None:
        total = len(self.data[self.section])
        shown = len(self.rows)
        label = dict(SECTIONS)[self.section]
        msg = f" {shown} of {total} {label.lower()}" if shown != total else f" {total} {label.lower()}"
        if self.section == "explore" and self.search_query:
            msg += f" for “{self.search_query}”"
        if self.section == "explore" and not self.search_query:
            msg = " Press / to search all of GitHub"
        if self.section == "local" and not self.rows:
            msg = f" No clones yet in {self.app.config.clone_dir} — select a repo and press c"
        if extra:
            msg += "   " + extra
        self.query_one("#statusbar", Static).update(msg)

    def current(self) -> dict | None:
        try:
            table = self.query_one("#table", DataTable)
        except Exception:
            return None
        if not self.rows or table.cursor_row is None:
            return None
        if 0 <= table.cursor_row < len(self.rows):
            return self.rows[table.cursor_row]
        return None

    def update_info(self) -> None:
        r = self.current()
        body = self.query_one("#info-body", Static)
        if not r:
            body.update(Text("Nothing selected", style="dim"))
            return
        t = Text()
        if self.section == "local":
            t.append(r["name"] + "\n", style="bold")
            t.append(r["path"] + "\n\n", style="dim")
            t.append("Branch   ", style="dim")
            t.append(r["branch"] + "\n", style="bold")
            t.append("Remote   ", style="dim")
            t.append((r["full_name"] or "none") + "\n")
            t.append("Changes  ", style="dim")
            t.append(f"{r['changes']} file(s)\n")
            if r["ahead"] or r["behind"]:
                t.append("Sync     ", style="dim")
                t.append(f"↑{r['ahead']} ahead  ↓{r['behind']} behind\n")
            t.append("\n⏎ open  ·  status, commit, push, pull, branches", style="dim italic")
        else:
            t.append(r["full_name"] + "\n", style="bold")
            t.append(("Private" if r.get("private") else "Public") + " repository", style="dim")
            if r.get("fork"):
                t.append(" · fork", style="dim")
            t.append("\n\n")
            if r.get("description"):
                t.append(r["description"] + "\n\n")
            t.append("★ ", style="#FBBF24")
            t.append(f"{compact(r.get('stargazers_count'))}   ")
            t.append("⑂ ", style="#22D3EE")
            t.append(f"{compact(r.get('forks_count'))}   ")
            t.append("◎ ", style="#34D399")
            t.append(f"{compact(r.get('open_issues_count'))} open\n\n")
            t.append("Language  ", style="dim")
            t.append_text(lang_text(r.get("language")))
            t.append("\nBranch    ", style="dim")
            t.append((r.get("default_branch") or "") + "\n")
            t.append("Pushed    ", style="dim")
            t.append(ago(r.get("pushed_at")) + "\n")
            if r.get("license"):
                t.append("License   ", style="dim")
                t.append((r["license"] or {}).get("spdx_id", "") + "\n")
            t.append("Local     ", style="dim")
            if self.is_cloned(r):
                t.append(str(self.local_path(r)) + "\n", style="#22D3EE")
            else:
                t.append("not cloned — press c\n", style="dim italic")
            topics = r.get("topics") or []
            if topics:
                t.append("\n")
                for tp in topics[:10]:
                    t.append(f" {tp} ", style="reverse")
                    t.append(" ")
        body.update(t)

    @on(DataTable.RowHighlighted)
    def highlighted(self) -> None:
        self.update_info()

    def on_resize(self, event: Resize) -> None:
        self.query_one("#info").set_class(event.size.width < 100, "hidden")

    # ----------------------------------------------------------- loading
    @work(group="load", exclusive=True)
    async def load(self, section: str, force: bool = False) -> None:
        table = self.query_one("#table", DataTable)
        if section in self.loaded and not force:
            self.render_table()
            return
        table.loading = True
        try:
            gh = self.app.gh
            if section == "repos":
                self.data["repos"] = await gh.repos()
            elif section == "starred":
                self.data["starred"] = await gh.starred()
            elif section == "explore":
                self.data["explore"] = await gh.search_repos(self.search_query) if self.search_query else []
            elif section == "local":
                self.data["local"] = await self.scan_local()
            self.loaded.add(section)
        except GitHubError as exc:
            if exc.status == 401:
                self.app.token_expired()
                return
            self.notify(str(exc), severity="error", timeout=8)
        finally:
            table.loading = False
        if section == self.section:
            self.render_table()

    async def scan_local(self) -> list[dict]:
        out = []
        for path in await gitops.find_local_repos(self.app.config.clone_path):
            try:
                st = await gitops.status(path)
            except Exception:
                continue
            url = await gitops.remote_url(path)
            out.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "branch": st.branch or "?",
                    "dirty": bool(st.files),
                    "changes": len(st.files),
                    "ahead": st.ahead,
                    "behind": st.behind,
                    "full_name": gitops.github_full_name(url) or "",
                }
            )
        return out

    # ----------------------------------------------------------- actions
    def action_section(self, name: str) -> None:
        if name == self.section:
            return
        self.section = name
        self.filter_text = ""
        f = self.query_one("#filter", Input)
        f.value = ""
        f.add_class("hidden")
        self.render_bars()
        self.query_one("#table", DataTable).focus()
        if name == "local":
            self.load("local", force=True)
        elif name == "explore" and not self.search_query:
            self.render_table()
            self.action_filter()
        else:
            self.load(name)

    def action_next_section(self) -> None:
        keys = [k for k, _ in SECTIONS]
        self.action_section(keys[(keys.index(self.section) + 1) % len(keys)])

    def action_cursor(self, where: str) -> None:
        table = self.query_one("#table", DataTable)
        if where == "down":
            table.action_cursor_down()
        elif where == "up":
            table.action_cursor_up()
        elif where == "top":
            table.move_cursor(row=0)
        elif where == "bottom":
            table.move_cursor(row=max(0, len(self.rows) - 1))

    def action_filter(self) -> None:
        f = self.query_one("#filter", Input)
        f.placeholder = "Search GitHub (e.g. 'textual language:python')…" if self.section == "explore" else "Filter…  (enter to apply, esc to clear)"
        f.value = self.search_query if self.section == "explore" else self.filter_text
        f.remove_class("hidden")
        f.focus()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        if self.section != "explore":
            self.filter_text = event.value
            self.render_table()
            self.render_bars()

    @on(Input.Submitted, "#filter")
    def filter_submitted(self, event: Input.Submitted) -> None:
        if self.section == "explore":
            self.search_query = event.value.strip()
            self.load("explore", force=True)
        if not event.value:
            event.input.add_class("hidden")
        self.query_one("#table", DataTable).focus()

    def action_clear_filter(self) -> None:
        f = self.query_one("#filter", Input)
        f.value = ""
        f.add_class("hidden")
        self.filter_text = ""
        self.render_bars()
        self.render_table()
        self.query_one("#table", DataTable).focus()

    def action_sort(self) -> None:
        self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        self.render_bars()
        self.render_table()

    def action_refresh(self) -> None:
        self.load(self.section, force=True)

    @on(DataTable.RowSelected)
    def row_selected(self) -> None:
        r = self.current()
        if not r:
            return
        if self.section == "local":
            self.open_local_path(r["path"])
        else:
            from .repo import RepoScreen

            self.app.push_screen(RepoScreen(r), lambda _: self.after_child())

    def after_child(self) -> None:
        if self.section == "local":
            self.load("local", force=True)
        else:
            self.render_table()

    def open_local_path(self, path: str) -> None:
        from .local import LocalRepoScreen

        self.app.push_screen(LocalRepoScreen(path), lambda _: self.after_child())

    def remote_selected(self) -> dict | None:
        r = self.current()
        if not r:
            self.notify("Nothing selected.", severity="warning")
            return None
        if self.section == "local":
            self.notify("Switch to Repositories (1) for GitHub actions.", severity="warning")
            return None
        return r

    def action_clone(self) -> None:
        r = self.remote_selected()
        if r:
            self.clone_repo(r)

    @work
    async def clone_repo(self, repo: dict, open_after: bool = True) -> None:
        dest = self.local_path(repo)
        if dest.exists():
            self.notify(f"{dest} already exists — press o to open it.", severity="warning")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = await self.app.push_screen_wait(
            GitRunDialog(
                f"⇣ Cloning {repo['full_name']}",
                ["clone", "--progress", repo["clone_url"], str(dest)],
                None,
                self.app.config.token,
            )
        )
        self.loaded.discard("local")
        self.render_table()
        if ok and open_after:
            self.open_local_path(str(dest))

    def action_open_local(self) -> None:
        r = self.current()
        if not r:
            return
        if self.section == "local":
            self.open_local_path(r["path"])
            return
        if self.is_cloned(r):
            self.open_local_path(str(self.local_path(r)))
        else:
            self.confirm_clone(r)

    @work
    async def confirm_clone(self, r: dict) -> None:
        if await self.app.push_screen_wait(
            ConfirmDialog("Not cloned yet", f"Clone [b]{r['full_name']}[/b] into\n{self.local_path(r)} ?")
        ):
            self.clone_repo(r)

    @work
    async def action_open_path(self) -> None:
        path = await self.app.push_screen_wait(
            InputDialog("Open local repository", "Path to a git working tree:", value=str(Path.cwd()))
        )
        if not path:
            return
        path = str(Path(path).expanduser())
        if not await gitops.is_repo(path):
            self.notify("Not a git repository.", severity="error")
            return
        self.open_local_path(await gitops.toplevel(path) or path)

    @work
    async def action_new_repo(self) -> None:
        spec = await self.app.push_screen_wait(NewRepoDialog())
        if not spec:
            return
        try:
            repo = await self.app.gh.create_repo(spec["name"], spec["description"], spec["private"], spec["auto_init"])
        except GitHubError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.notify(f"Created {repo['full_name']} ✨")
        self.data["repos"].insert(0, repo)
        if self.section != "repos":
            self.action_section("repos")
        else:
            self.render_table()
        if spec["clone"]:
            self.clone_repo(repo)

    @work
    async def action_star(self) -> None:
        r = self.remote_selected()
        if not r:
            return
        try:
            starred = await self.app.gh.is_starred(r["full_name"])
            await self.app.gh.set_star(r["full_name"], not starred)
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        r["stargazers_count"] = (r.get("stargazers_count") or 0) + (-1 if starred else 1)
        self.loaded.discard("starred")
        self.notify(("Unstarred " if starred else "★ Starred ") + r["full_name"])
        self.render_table()

    @work
    async def action_fork(self) -> None:
        r = self.remote_selected()
        if not r:
            return
        if not await self.app.push_screen_wait(ConfirmDialog("Fork", f"Fork [b]{r['full_name']}[/b] to your account?")):
            return
        try:
            fork = await self.app.gh.fork(r["full_name"])
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Forked to {fork['full_name']} (may take a moment to appear)")
        self.loaded.discard("repos")

    @work
    async def action_delete(self) -> None:
        r = self.remote_selected()
        if not r:
            return
        if not (r.get("permissions") or {}).get("admin"):
            self.notify("You need admin rights to delete this repository.", severity="error")
            return
        ok = await self.app.push_screen_wait(
            TypeToConfirmDialog(
                "⚠ Delete repository",
                "This permanently deletes the repository on GitHub,\nincluding issues, PRs and wiki. This cannot be undone.\n(Your local clone, if any, is kept.)",
                r["full_name"],
            )
        )
        if not ok:
            return
        try:
            await self.app.gh.delete_repo(r["full_name"])
        except GitHubError as exc:
            msg = str(exc)
            if exc.status == 403:
                msg += " — your token needs the delete_repo scope."
            self.notify(msg, severity="error", timeout=10)
            return
        for key in ("repos", "starred", "explore"):
            self.data[key] = [x for x in self.data[key] if x.get("full_name") != r["full_name"]]
        self.notify(f"Deleted {r['full_name']}")
        self.render_table()

    def action_web(self) -> None:
        r = self.current()
        if not r:
            return
        url = r.get("html_url") or (f"https://github.com/{r['full_name']}" if r.get("full_name") else "")
        if url:
            self.app.push_screen(LinkDialog(r.get("full_name") or r.get("name"), url))

    def action_copy_url(self) -> None:
        r = self.current()
        if not r:
            return
        url = r.get("clone_url") or (f"https://github.com/{r['full_name']}.git" if r.get("full_name") else r.get("path"))
        self.app.copy_to_clipboard(url)
        self.notify(f"Copied {url}")

    def action_theme(self) -> None:
        self.app.action_pick_theme()

    def action_settings(self) -> None:
        self.app.action_settings()

    def action_help(self) -> None:
        self.app.push_screen(HelpDialog(HELP))

    def on_screen_resume(self) -> None:
        self.render_bars()
