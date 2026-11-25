"""gitui application entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from textual import work
from textual.app import App
from textual.binding import Binding

from . import __version__, gitops
from .config import Config
from .github import GitHub, GitHubError
from .ui import THEMES


class GitUI(App):
    TITLE = "gitui"
    CSS_PATH = "styles.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+t", "pick_theme", "Theme", show=False),
    ]

    def __init__(self, open_path: str | None = None):
        super().__init__()
        self.config = Config.load()
        self.gh: GitHub | None = None
        self.user: dict | None = None
        self.open_path = open_path
        for theme in THEMES:
            self.register_theme(theme)

    def on_mount(self) -> None:
        try:
            self.theme = self.config.theme
        except Exception:
            self.theme = "gitui"
        if self.config.token:
            self.restore_session()
        else:
            self.show_login()

    def show_login(self) -> None:
        from .screens.login import LoginScreen

        self.push_screen(LoginScreen())

    @work(exclusive=True)
    async def restore_session(self) -> None:
        gh = GitHub(self.config.token)
        try:
            self.user = await gh.user()
        except GitHubError as exc:
            await gh.close()
            if exc.status == 401:
                self.notify("Your GitHub session expired — please sign in again.", severity="warning")
                self.config.sign_out()
            else:
                self.notify(f"{exc}", severity="error", timeout=10)
            self.show_login()
            return
        self.gh = gh
        self.show_main()

    def complete_sign_in(self, token: str, user: dict, method: str) -> None:
        self.config.token = token
        self.config.login = user.get("login", "")
        self.config.auth_method = method
        self.config.save()
        self.user = user
        self.gh = GitHub(token)
        self.notify(f"Welcome, {user.get('name') or user.get('login')}! 👋", timeout=4)
        self.show_main()

    def show_main(self) -> None:
        from .screens.dashboard import Dashboard
        from .screens.local import LocalRepoScreen

        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(Dashboard())
        if self.open_path:
            self.push_screen(LocalRepoScreen(self.open_path))
            self.open_path = None

    def token_expired(self) -> None:
        self.notify("GitHub rejected the token — please sign in again.", severity="error")
        self.sign_out()

    def sign_out(self) -> None:
        self.config.sign_out()
        self.gh = None
        self.user = None
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.show_login()

    # ------------------------------------------------------------ global
    @work
    async def action_pick_theme(self) -> None:
        from .screens.dialogs import ThemeDialog

        choice = await self.push_screen_wait(ThemeDialog())
        if choice:
            self.theme = choice
            self.config.theme = choice
            self.config.save()
            self.notify(f"Theme: {choice}")

    @work
    async def action_settings(self) -> None:
        from .screens.dialogs import ConfirmDialog, SettingsDialog

        res = await self.push_screen_wait(SettingsDialog())
        if res == "saved":
            self.notify("Settings saved")
            from .screens.dashboard import Dashboard

            for screen in self.screen_stack:
                if isinstance(screen, Dashboard):
                    screen.loaded.discard("local")
                    screen.render_bars()
                    screen.render_table()
        elif res == "theme":
            self.action_pick_theme()
        elif res == "signout":
            if await self.push_screen_wait(ConfirmDialog("Sign out", "Remove the saved GitHub token from this device?", danger=True)):
                self.sign_out()

    async def on_unmount(self) -> None:
        if self.gh:
            await self.gh.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="gitui",
        description="A sleek, keyboard-only GitHub client for the terminal.",
    )
    parser.add_argument("path", nargs="?", help="open a local git repository directly (e.g. 'gitui .')")
    parser.add_argument("--version", action="version", version=f"gitui {__version__}")
    parser.add_argument("--logout", action="store_true", help="forget the saved GitHub sign-in and exit")
    args = parser.parse_args(argv)

    if args.logout:
        Config.load().sign_out()
        print("Signed out. Your token was removed from this device.")
        return

    if not gitops.git_available():
        print("gitui needs git. Install it with: sudo apt install git", file=sys.stderr)
        sys.exit(1)

    open_path = None
    if args.path:
        p = Path(args.path).expanduser().resolve()
        top = asyncio.run(gitops.toplevel(p)) if p.is_dir() else None
        if not top:
            print(f"gitui: {p} is not inside a git repository", file=sys.stderr)
            sys.exit(1)
        open_path = top

    GitUI(open_path=open_path).run(mouse=False)


if __name__ == "__main__":
    main()
