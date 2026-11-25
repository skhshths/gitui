"""Issue / pull request detail with comments."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, Static

from ..github import GitHubError
from ..ui import ago
from .dialogs import ChoiceDialog, ConfirmDialog, LinkDialog, TextDialog


class IssueScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("m", "comment", "Comment"),
        Binding("x", "toggle_state", "Close/Reopen"),
        Binding("M", "merge", "Merge PR"),
        Binding("w", "web", "Link"),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "scroll('down')", show=False),
        Binding("k", "scroll('up')", show=False),
        Binding("g", "scroll('home')", show=False),
        Binding("G", "scroll('end')", show=False),
    ]

    def __init__(self, full_name: str, issue: dict, is_pr: bool):
        super().__init__()
        self.full_name = full_name
        self.issue = issue
        self.is_pr = is_pr
        self.changed = False

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with VerticalScroll(id="issue-scroll", can_focus=True):
            yield Static(id="issue-head")
            yield Markdown(id="issue-body")
            yield Static("", id="comments-head", classes="section-title")
            yield VerticalScroll(id="comments")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#issue-scroll").focus()
        self.render_head()
        self.load()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "merge" and not self.is_pr:
            return False
        return True

    def render_head(self) -> None:
        it = self.issue
        kind = "Pull request" if self.is_pr else "Issue"
        self.query_one("#topbar", Static).update(
            Text.assemble((" ◆ gitui ", "bold reverse"), ("  ", ""), (self.full_name, "bold"), (f"  ›  {kind} #{it['number']}", ""), ("   esc back", "dim"))
        )
        state = it.get("state", "open")
        if self.is_pr and it.get("merged_at"):
            badge = (" ⇄ Merged ", "bold #FFFFFF on #8B5CF6")
        elif state == "open":
            badge = (" ◎ Open ", "bold #000000 on #34D399")
        else:
            badge = (" ✔ Closed ", "bold #FFFFFF on #EF4444")
        t = Text()
        t.append(it["title"] + "\n", style="bold")
        t.append(*badge)
        t.append(f"  @{(it.get('user') or {}).get('login', '?')}", style="#22D3EE")
        t.append(f" opened {ago(it.get('created_at'))}", style="dim")
        if self.is_pr:
            head = (it.get("head") or {}).get("label", "")
            base = (it.get("base") or {}).get("label", "")
            t.append(f"\n{head} → {base}", style="dim")
            if "additions" in it:
                t.append("   ")
                t.append(f"+{it['additions']}", style="#34D399")
                t.append(" ")
                t.append(f"-{it['deletions']}", style="#F87171")
                t.append(f"  in {it.get('changed_files', 0)} files", style="dim")
            if it.get("mergeable_state") and state == "open":
                t.append(f"   mergeable: {it['mergeable_state']}", style="dim")
        labels = it.get("labels") or []
        if labels:
            t.append("\n")
            for lb in labels:
                t.append(f" {lb['name']} ", style=f"#000000 on #{lb.get('color') or '888888'}")
                t.append(" ")
        self.query_one("#issue-head", Static).update(t)

    @work(exclusive=True)
    async def load(self) -> None:
        gh = self.app.gh
        try:
            if self.is_pr:
                self.issue = await gh.pull(self.full_name, self.issue["number"])
                self.render_head()
            comments = await gh.comments(self.full_name, self.issue["number"])
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        await self.query_one("#issue-body", Markdown).update(self.issue.get("body") or "_No description provided._")
        self.query_one("#comments-head", Static).update(f"💬 {len(comments)} comment(s)   ·   m to add one")
        box = self.query_one("#comments", VerticalScroll)
        await box.remove_children()
        for c in comments:
            head = Static(
                Text.assemble((f"@{(c.get('user') or {}).get('login', '?')}", "bold #22D3EE"), (f"  {ago(c.get('created_at'))}", "dim")),
                classes="comment-head",
            )
            await box.mount(head)
            await box.mount(Markdown(c.get("body") or "", classes="comment"))

    def action_back(self) -> None:
        self.dismiss(self.changed)

    def action_scroll(self, where: str) -> None:
        sc = self.query_one("#issue-scroll", VerticalScroll)
        {"down": sc.scroll_down, "up": sc.scroll_up, "home": sc.scroll_home, "end": sc.scroll_end}[where]()

    def action_refresh(self) -> None:
        self.load()

    @work
    async def action_comment(self) -> None:
        body = await self.app.push_screen_wait(TextDialog(f"Comment on #{self.issue['number']}", with_title=False, body_label="Comment (markdown)"))
        if not body:
            return
        try:
            await self.app.gh.comment(self.full_name, self.issue["number"], body)
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify("Comment posted")
        self.changed = True
        self.load()
        self.query_one("#issue-scroll", VerticalScroll).scroll_end()

    @work
    async def action_toggle_state(self) -> None:
        new = "closed" if self.issue.get("state") == "open" else "open"
        verb = "Close" if new == "closed" else "Reopen"
        if not await self.app.push_screen_wait(ConfirmDialog(verb, f"{verb} #{self.issue['number']}?")):
            return
        try:
            self.issue = {**self.issue, **(await self.app.gh.set_issue_state(self.full_name, self.issue["number"], new))}
        except GitHubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.changed = True
        self.notify(f"{verb}d #{self.issue['number']}")
        self.load()
        self.render_head()

    @work
    async def action_merge(self) -> None:
        if not self.is_pr:
            return
        if self.issue.get("merged_at") or self.issue.get("state") != "open":
            self.notify("This pull request is not open.", severity="warning")
            return
        method = await self.app.push_screen_wait(
            ChoiceDialog(
                f"Merge #{self.issue['number']}",
                [("merge", "Create a merge commit"), ("squash", "Squash and merge"), ("rebase", "Rebase and merge")],
            )
        )
        if not method:
            return
        try:
            await self.app.gh.merge_pull(self.full_name, self.issue["number"], method)
        except GitHubError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.changed = True
        self.notify(f"Merged #{self.issue['number']} 🎉")
        self.load()

    def action_web(self) -> None:
        self.app.push_screen(LinkDialog(f"#{self.issue['number']}", self.issue.get("html_url", "")))
