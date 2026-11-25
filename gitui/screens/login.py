"""First-run sign-in screen."""

from __future__ import annotations

import asyncio
import shutil

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ..config import OAUTH_SCOPES
from ..github import GitHubError, device_code_poll, device_code_start, validate_token
from ..ui import qr_text
from .dialogs import InputDialog

LOGO = r"""
   ██████╗ ██╗████████╗██╗   ██╗██╗
  ██╔════╝ ██║╚══██╔══╝██║   ██║██║
  ██║  ███╗██║   ██║   ██║   ██║██║
  ██║   ██║██║   ██║   ██║   ██║██║
  ╚██████╔╝██║   ██║   ╚██████╔╝██║
   ╚═════╝ ╚═╝   ╚═╝    ╚═════╝ ╚═╝"""

TOKEN_URL = (
    "https://github.com/settings/tokens/new?description=gitui"
    "&scopes=repo,read:org,workflow,delete_repo,gist"
)


class LoginScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Center(id="login-wrap"):
            with VerticalScroll(id="login-card"):
                yield Static(LOGO, id="logo")
                yield Static("A sleek, keyboard-only GitHub client for your terminal", id="tagline")
                with Vertical(id="login-menu"):
                    yield Static("Sign in to GitHub — you only need to do this once.", classes="box-body")
                    yield OptionList(id="methods")
                with Vertical(id="device", classes="hidden"):
                    with Horizontal(id="device-row"):
                        with Vertical(id="device-left"):
                            yield Static(id="device-text")
                            yield LoadingIndicator()
                            yield Static("Waiting for you to approve on GitHub…\n[b]esc[/b] cancel", classes="hint")
                        yield Static(id="device-qr")
                with Vertical(id="token", classes="hidden"):
                    yield Static(id="token-text")
                    yield Input(placeholder="ghp_… or github_pat_…", password=True, id="token-input")
                    yield Static("[b]enter[/b] sign in   [b]esc[/b] back", classes="hint")
                    yield Static(id="token-qr")
        yield Footer()

    def on_mount(self) -> None:
        ol = self.query_one("#methods", OptionList)
        ol.add_option(Option(Text.assemble(("📱  Sign in with GitHub", "bold"), ("  (code + QR, approve on your phone)" if self.app.config.effective_client_id else "  (code + QR — needs one-time setup, see README)", "dim")), id="device"))
        if shutil.which("gh"):
            ol.add_option(Option(Text.assemble(("⚡  Use my GitHub CLI login", "bold"), ("  (gh auth token)", "dim")), id="gh"))
        ol.add_option(Option(Text.assemble(("🔑  Paste a personal access token", "bold")), id="token"))
        ol.highlighted = 0
        ol.focus()

    def show(self, panel: str) -> None:
        for pid in ("login-menu", "device", "token"):
            self.query_one(f"#{pid}").set_class(pid != panel, "hidden")
        # free up vertical space for the code / QR on small screens
        compact = panel != "login-menu"
        self.query_one("#logo").set_class(compact, "hidden")
        self.query_one("#tagline").set_class(compact, "hidden")

    def action_back(self) -> None:
        self.workers.cancel_group(self, "auth")
        self.show("login-menu")
        self.query_one("#methods", OptionList).focus()

    @on(OptionList.OptionSelected, "#methods")
    def method_chosen(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "device":
            self.start_device()
        elif choice == "gh":
            self.use_gh()
        else:
            self.start_token()

    # ------------------------------------------------------------ device
    @work(group="auth", exclusive=True)
    async def start_device(self) -> None:
        cfg = self.app.config
        client_id = cfg.effective_client_id
        if not client_id:
            value = await self.app.push_screen_wait(
                InputDialog(
                    "One-time setup: OAuth client ID",
                    "Phone/browser sign-in needs a GitHub OAuth App client ID with\n"
                    "'Enable Device Flow' ticked. Create one on any device at\n"
                    "[b]github.com/settings/developers[/b] → New OAuth App\n"
                    "(callback URL can be http://localhost). See README for details.\n"
                    "Or press esc and choose 'Paste a personal access token' instead.",
                    placeholder="Ov23li…",
                )
            )
            if not value:
                return
            cfg.client_id = value.strip()
            cfg.save()
            client_id = cfg.client_id
        try:
            data = await device_code_start(client_id, OAUTH_SCOPES)
        except GitHubError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        uri = data.get("verification_uri", "https://github.com/login/device")
        code = data["user_code"]
        self.show("device")
        self.query_one("#device-text", Static).update(
            Text.assemble(
                ("On your phone or any computer, open\n", ""),
                (uri, "bold underline"),
                ("\nand enter this code:\n\n", ""),
                (f"  {code}  ", "bold reverse"),
            )
        )
        qr = qr_text(uri)
        self.query_one("#device-qr", Static).update(qr if qr is not None else "")
        try:
            token = await device_code_poll(
                client_id, data["device_code"], int(data.get("interval", 5)), int(data.get("expires_in", 900))
            )
        except asyncio.CancelledError:
            raise
        except GitHubError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self.action_back()
            return
        await self.finish(token, "device")

    # ------------------------------------------------------------ gh cli
    @work(group="auth", exclusive=True)
    async def use_gh(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "gh", "auth", "token",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        token = out.decode().strip()
        if proc.returncode != 0 or not token:
            self.notify("GitHub CLI is not logged in. Run 'gh auth login' first.", severity="error")
            return
        await self.finish(token, "gh")

    # ------------------------------------------------------------ token
    def start_token(self) -> None:
        self.show("token")
        self.query_one("#token-text", Static).update(
            Text.assemble(
                ("Create a token (classic) on any device — scopes are pre-filled:\n", ""),
                (TOKEN_URL, "bold underline"),
                ("\nThen paste it below.", ""),
            )
        )
        qr = qr_text(TOKEN_URL)
        self.query_one("#token-qr", Static).update(qr if qr is not None else "")
        self.query_one("#token-input", Input).focus()

    @on(Input.Submitted, "#token-input")
    def token_submitted(self, event: Input.Submitted) -> None:
        token = event.value.strip()
        if token:
            self.run_token(token)

    @work(group="auth", exclusive=True)
    async def run_token(self, token: str) -> None:
        await self.finish(token, "token")

    # ------------------------------------------------------------ finish
    async def finish(self, token: str, method: str) -> None:
        try:
            user = await validate_token(token)
        except GitHubError as exc:
            self.notify(f"Sign-in failed: {exc}", severity="error", timeout=8)
            return
        self.app.complete_sign_in(token, user, method)
