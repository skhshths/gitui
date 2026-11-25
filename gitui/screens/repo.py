"""Remote repository view: README, issues, pull requests, branches, commits, releases."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Markdown, Static, TabbedContent, TabPane

from ..github import GitHubError
from ..ui import ago, compact, lang_text
from .dialogs import ConfirmDialog, GitRunDialog, HelpDialog, LinkDialog, TextDialog

TABS = ["overview", "issues", "pulls", "branches", "commits", "releases"]

HELP = [
    ("Repository", [
        ("1-6", "Overview / Issues / PRs / Branches / Commits / Releases"),
        ("← →  h l", "Previous / next tab"),
        ("↑ ↓  j k", "Move / scroll"),
        ("enter", "Open issue or pull request"),
        ("i", "New issue"),
        ("a", "Issues/PRs: cycle open / closed / all"),
        ("c", "Clone"),
        ("o", "Open local clone"),
        ("*", "Star / unstar"),
        ("w", "Show link + QR code"),
        ("r", "Refresh tab"),
        ("esc", "Back"),
    ]),
]


class RepoScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("1", "tab(0)", "Overview", show=False),
        Binding("2", "tab(1)", "Issues", show=False),
        Binding("3", "tab(2)", "PRs", show=False),
        Binding("4", "tab(3)", "Branches", show=False),
        Binding("5", "tab(4)", "Commits", show=False),
        Binding("6", "tab(5)", "Releases", show=False),
        Binding("left,h", "step(-1)", "Prev tab", show=False),
        Binding("right,l", "step(1)", "Next tab", show=False),
        Binding("j", "down", show=False),
        Binding("k", "up", show=False),
        Binding("i", "new_issue", "New issue"),
        Binding("a", "cycle_state", "Open/closed", show=False),
        Binding("c", "clone", "Clone"),
        Binding("o", "open_local", "Local"),
        Binding("asterisk", "star", "Star", show=False),
        Binding("w", "web", "Link"),
        Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, repo: dict):
        super().__init__()
        self.repo = repo
        self.full_name = repo["full_name"]
        self.loaded: set[str] = set()
        self.items: dict[str, list[dict]] = {t: [] for t in TABS}
        self.state = {"issues": "open", "pulls": "open"}

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with TabbedContent(id="tabs-content", initial="overview"):
            with TabPane("1 Overview", id="overview"):
                with VerticalScroll(id="readme-scroll", can_focus=True):
                    yield Static(id="repo-summary")
                    yield Markdown("Loading README…", id="readme")
            for tid, label in (
                ("issues", "2 Issues"), ("pulls", "3 Pull requests"), ("branches", "4 Branches"),
                ("commits", "5 Commits"), ("releases", "6 Releases"),
            ):
                with TabPane(label, id=tid):
                    yield DataTable(id=f"t-{tid}", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        r = self.repo
        top = Text.assemble(
            (" ◆ gitui ", "bold reverse"), ("  ", ""),
            (r["full_name"], "bold"), ("   ", ""),
            ("🔒 private" if r.get("private") else "◉ public", "dim"), ("   ", ""),
            ("★ " + compact(r.get("stargazers_count")), "#FBBF24"), ("   esc back", "dim"),
        )
        self.query_one("#topbar", Static).update(top)
        s = Text()
        if r.get("description"):
            s.append(r["description"] + "\n", style="italic")
        s.append_text(lang_text(r.get("language")))
        s.append(f"   ⑂ {compact(r.get('forks_count'))}   ◎ {compact(r.get('open_issues_count'))} open   ")
        s.append(f"default: {r.get('default_branch', '')}   pushed {ago(r.get('pushed_at'))}", style="dim")
        s.append("\n" + r.get("clone_url", ""), style="dim underline")
        self.query_one("#repo-summary", Static).update(s)
        self.query_one("#readme-scroll").focus()
        self.load("overview")

    # ------------------------------------------------------------ tabs
    @property
    def active(self) -> str:
        return self.query_one(TabbedContent).active or "overview"

    def action_tab(self, idx: int) -> None:
        self.query_one(TabbedContent).active = TABS[idx]

    def action_step(self, delta: int) -> None:
        idx = TABS.index(self.active)
        self.action_tab((idx + delta) % len(TABS))

    @on(TabbedContent.TabActivated)
    def tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = self.active
        if tab == "overview":
            self.query_one("#readme-scroll").focus()
        else:
            self.query_one(f"#t-{tab}", DataTable).focus()
        self.load(tab)

    def action_down(self) -> None:
        w = self.focused
        if isinstance(w, DataTable):
            w.action_cursor_down()
        elif w is not None and hasattr(w, "scroll_down"):
            w.scroll_down()

    def action_up(self) -> None:
        w = self.focused
        if isinstance(w, DataTable):
            w.action_cursor_up()
        elif w is not None and hasattr(w, "scroll_up"):
            w.scroll_up()

    def action_refresh(self) -> None:
        self.load(self.active, force=True)

    def action_cycle_state(self) -> None:
        tab = self.active
        if tab not in self.state:
            return
        order = ["open", "closed", "all"]
        self.state[tab] = order[(order.index(self.state[tab]) + 1) % 3]
        self.notify(f"Showing {self.state[tab]} {'issues' if tab == 'issues' else 'pull requests'}")
        self.load(tab, force=True)

    # ------------------------------------------------------------ loading
    @work(group="tabload")
    async def load(self, tab: str, force: bool = False) -> None:
        if tab in self.loaded and not force:
            return
        gh = self.app.gh
        try:
            if tab == "overview":
                md = await gh.readme(self.full_name)
                await self.query_one("#readme", Markdown).update(md)
                self.loaded.add(tab)
                return
            table = self.query_one(f"#t-{tab}", DataTable)
            table.loading = True
            try:
                if tab == "issues":
                    data = await gh.issues(self.full_name, self.state["issues"])
                elif tab == "pulls":
                    data = await gh.pulls(self.full_name, self.state["pulls"])
                elif tab == "branches":
                    data = await gh.branches(self.full_name)
                elif tab == "commits":
                    data = await gh.commits(self.full_name)
                else:
                    data = await gh.releases(self.full_name)
            finally:
                table.loading = False
            self.items[tab] = data
            self.loaded.add(tab)
            self.fill(tab)
        except GitHubError as exc:
            if exc.status == 409 and tab == "commits":
                self.items[tab] = []
                self.fill(tab)
                return
            self.notify(str(exc), severity="error", timeout=8)

    def fill(self, tab: str) -> None:
        table = self.query_one(f"#t-{tab}", DataTable)
        table.clear(columns=True)
        data = self.items[tab]
        if tab in ("issues", "pulls"):
            table.add_columns("", "#", "Title", "Author", "💬", "Updated")
            for it in data:
                if tab == "pulls":
                    icon = Text("⇄", style="#A78BFA" if it.get("merged_at") else ("#34D399" if it["state"] == "open" else "#F87171"))
                else:
                    icon = Text("◎" if it["state"] == "open" else "✔", style="#34D399" if it["state"] == "open" else "#A78BFA")
                title = Text(it["title"], style="bold")
                for lb in (it.get("labels") or [])[:3]:
                    title.append("  ")
                    color = "#" + (lb.get("color") or "888888")
                    title.append(f" {lb['name']} ", style=f"#000000 on {color}")
                if it.get("draft"):
                    title.append("  draft", style="dim italic")
                table.add_row(
                    icon, Text(f"#{it['number']}", style="dim"), title,
                    Text("@" + (it.get("user") or {}).get("login", "?"), style="#22D3EE"),
                    str(it.get("comments", "")) if tab == "issues" else "",
                    Text(ago(it.get("updated_at")), style="dim"),
                    key=str(it["number"]),
                )
            if not data:
                table.add_row("", "", Text(f"No {self.state[tab]} {'issues' if tab == 'issues' else 'pull requests'} — press a to cycle, i for new issue", style="dim italic"), "", "", "")
        elif tab == "branches":
            table.add_columns("", "Branch", "Commit", "")
            default = self.repo.get("default_branch")
            for b in data:
                table.add_row(
                    Text("★" if b["name"] == default else "⎇", style="#FBBF24" if b["name"] == default else "dim"),
                    Text(b["name"], style="bold"),
                    Text((b.get("commit") or {}).get("sha", "")[:7], style="dim"),
                    Text("protected", style="#F472B6") if b.get("protected") else "",
                )
        elif tab == "commits":
            table.add_columns("SHA", "Message", "Author", "When")
            for c in data:
                commit = c.get("commit") or {}
                msg = (commit.get("message") or "").splitlines()[0] if commit.get("message") else ""
                author = (c.get("author") or {}).get("login") or (commit.get("author") or {}).get("name", "")
                table.add_row(
                    Text(c["sha"][:7], style="#FBBF24"), msg,
                    Text(author, style="#22D3EE"),
                    Text(ago((commit.get("author") or {}).get("date")), style="dim"),
                )
            if not data:
                table.add_row("", Text("Empty repository", style="dim italic"), "", "")
        elif tab == "releases":
            table.add_columns("Tag", "Name", "Published", "")
            for rl in data:
                table.add_row(
                    Text(rl.get("tag_name", ""), style="bold #34D399"),
                    rl.get("name") or "",
                    Text(ago(rl.get("published_at")), style="dim"),
                    Text("pre-release", style="#FBBF24") if rl.get("prerelease") else "",
                )
            if not data:
                table.add_row("", Text("No releases", style="dim italic"), "", "")

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        tab = self.active
        if tab not in ("issues", "pulls"):
            if tab == "commits" and self.items["commits"]:
                c = self.items["commits"][event.cursor_row]
                self.app.push_screen(LinkDialog(c["sha"][:7], c["html_url"]))
            return
        data = self.items[tab]
        if not data or event.cursor_row >= len(data):
            return
        from .issue import IssueScreen

        self.app.push_screen(
            IssueScreen(self.full_name, data[event.cursor_row], is_pr=(tab == "pulls")),
            lambda changed: self.load(tab, force=True) if changed else None,
        )

    # ------------------------------------------------------------ actions
    @work
    async def action_new_issue(self) -> None:
        res = await self.app.push_screen_wait(TextDialog(f"New issue in {self.full_name}", title_placeholder="Issue title"))
        if not res:
            return
        title, body = res
        try:
            issue = await self.app.gh.create_issue(self.full_name, title, body)
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Opened issue #{issue['number']}")
        self.action_tab(1)
        self.load("issues", force=True)

    def dashboard(self):
        from .dashboard import Dashboard

        for s in self.app.screen_stack:
            if isinstance(s, Dashboard):
                return s
        return None

    @work
    async def action_clone(self) -> None:
        dest = self.app.config.clone_path / self.repo["name"]
        if dest.exists():
            self.notify(f"{dest} already exists — press o.", severity="warning")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = await self.app.push_screen_wait(
            GitRunDialog(f"⇣ Cloning {self.full_name}", ["clone", "--progress", self.repo["clone_url"], str(dest)], None, self.app.config.token)
        )
        if ok:
            self.notify(f"Cloned to {dest} — press o to open")
            d = self.dashboard()
            if d:
                d.loaded.discard("local")

    @work
    async def action_open_local(self) -> None:
        dest = self.app.config.clone_path / self.repo["name"]
        if not (dest / ".git").exists():
            if await self.app.push_screen_wait(ConfirmDialog("Not cloned yet", f"Clone into {dest} ?")):
                self.action_clone()
            return
        from .local import LocalRepoScreen

        self.app.push_screen(LocalRepoScreen(str(dest)))

    @work
    async def action_star(self) -> None:
        try:
            starred = await self.app.gh.is_starred(self.full_name)
            await self.app.gh.set_star(self.full_name, not starred)
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(("Unstarred " if starred else "★ Starred ") + self.full_name)

    def action_web(self) -> None:
        self.app.push_screen(LinkDialog(self.full_name, self.repo.get("html_url") or f"https://github.com/{self.full_name}"))

    def action_help(self) -> None:
        self.app.push_screen(HelpDialog(HELP))
