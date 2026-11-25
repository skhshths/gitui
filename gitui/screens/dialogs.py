"""Modal dialogs. Every dialog is fully keyboard driven."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    Log,
    OptionList,
    Static,
    Switch,
    TextArea,
)
from textual.widgets.option_list import Option

from .. import gitops
from ..ui import qr_text


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CLASSES = "dialog"

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------
class ConfirmDialog(Dialog):
    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, message: str, danger: bool = False):
        super().__init__()
        self.title_text = title
        self.message = message
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="box" + (" danger" if self.danger else "")):
            yield Label(self.title_text, classes="box-title")
            yield Static(self.message, classes="box-body")
            yield Static("[b]y[/b] confirm   [b]n[/b]/[b]esc[/b] cancel", classes="hint")

    def action_yes(self) -> None:
        self.dismiss(True)


class TypeToConfirmDialog(Dialog):
    """For destructive actions: the user must type the exact name."""

    def __init__(self, title: str, message: str, expected: str):
        super().__init__()
        self.title_text = title
        self.message = message
        self.expected = expected

    def compose(self) -> ComposeResult:
        with Vertical(classes="box danger"):
            yield Label(self.title_text, classes="box-title")
            yield Static(self.message, classes="box-body")
            yield Static(f"Type [b]{self.expected}[/b] to confirm:", classes="box-body")
            yield Input(placeholder=self.expected, id="confirm-input")
            yield Static("[b]enter[/b] confirm   [b]esc[/b] cancel", classes="hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        if event.value.strip() == self.expected:
            self.dismiss(True)
        else:
            self.notify("Name does not match.", severity="error")


class InputDialog(Dialog):
    def __init__(
        self,
        title: str,
        prompt: str = "",
        value: str = "",
        placeholder: str = "",
        password: bool = False,
    ):
        super().__init__()
        self.title_text = title
        self.prompt = prompt
        self.value = value
        self.placeholder = placeholder
        self.password = password

    def compose(self) -> ComposeResult:
        with Vertical(classes="box"):
            yield Label(self.title_text, classes="box-title")
            if self.prompt:
                yield Static(self.prompt, classes="box-body")
            yield Input(value=self.value, placeholder=self.placeholder, password=self.password)
            yield Static("[b]enter[/b] ok   [b]esc[/b] cancel", classes="hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class TextDialog(Dialog):
    """Title + multi-line body (issues, comments, commit messages)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit", priority=True),
        Binding("ctrl+d", "submit", "Submit", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        with_title: bool = True,
        title_placeholder: str = "Title",
        body: str = "",
        body_label: str = "Body (markdown)",
    ):
        super().__init__()
        self.title_text = title
        self.with_title = with_title
        self.title_placeholder = title_placeholder
        self.body = body
        self.body_label = body_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="box wide"):
            yield Label(self.title_text, classes="box-title")
            if self.with_title:
                yield Input(placeholder=self.title_placeholder, id="title")
            yield Static(self.body_label, classes="field-label")
            yield TextArea(self.body, id="body", soft_wrap=True, show_line_numbers=False)
            yield Static(
                "[b]tab[/b] next field   [b]ctrl+s[/b] submit   [b]esc[/b] cancel", classes="hint"
            )

    @on(Input.Submitted)
    def next_field(self) -> None:
        self.query_one("#body", TextArea).focus()

    def action_submit(self) -> None:
        body = self.query_one("#body", TextArea).text
        if self.with_title:
            title = self.query_one("#title", Input).value.strip()
            if not title:
                self.notify("A title is required.", severity="warning")
                self.query_one("#title", Input).focus()
                return
            self.dismiss((title, body))
        else:
            if not body.strip():
                self.notify("Nothing to submit.", severity="warning")
                return
            self.dismiss(body)


class NewRepoDialog(Dialog):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Create", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="box"):
            yield Label("✦ New repository", classes="box-title")
            yield Static("Name", classes="field-label")
            yield Input(placeholder="my-awesome-project", id="name")
            yield Static("Description", classes="field-label")
            yield Input(placeholder="optional", id="desc")
            with Horizontal(classes="switch-row"):
                yield Switch(value=True, id="private")
                yield Label("Private")
            with Horizontal(classes="switch-row"):
                yield Switch(value=True, id="init")
                yield Label("Add a README")
            with Horizontal(classes="switch-row"):
                yield Switch(value=True, id="clone")
                yield Label("Clone it after creating")
            yield Static(
                "[b]tab[/b] move   [b]space[/b] toggle   [b]ctrl+s[/b] create   [b]esc[/b] cancel",
                classes="hint",
            )

    @on(Input.Submitted)
    def next_field(self) -> None:
        self.focus_next()

    def action_submit(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        if not name:
            self.notify("Name is required.", severity="warning")
            return
        self.dismiss(
            {
                "name": name,
                "description": self.query_one("#desc", Input).value.strip(),
                "private": self.query_one("#private", Switch).value,
                "auto_init": self.query_one("#init", Switch).value,
                "clone": self.query_one("#clone", Switch).value,
            }
        )


class ChoiceDialog(Dialog):
    """Pick one option from a list. Returns the option id."""

    def __init__(self, title: str, options: list[tuple[str, str]], hint: str = ""):
        super().__init__()
        self.title_text = title
        self.options = options
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="box"):
            yield Label(self.title_text, classes="box-title")
            yield OptionList(*[Option(label, id=oid) for oid, label in self.options])
            yield Static(self.hint or "[b]↑↓[/b] move   [b]enter[/b] choose   [b]esc[/b] cancel", classes="hint")

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ThemeDialog(Dialog):
    """Live-previewing theme picker. Esc restores the original theme."""

    def __init__(self) -> None:
        super().__init__()
        self.original = ""

    def compose(self) -> ComposeResult:
        names = sorted(self.app.available_themes.keys(), key=lambda n: (not n.startswith("gitui"), n))
        self.original = self.app.theme
        with Vertical(classes="box"):
            yield Label("🎨 Choose a theme", classes="box-title")
            ol = OptionList(*[Option(n, id=n) for n in names], id="themes")
            yield ol
            yield Static("[b]↑↓[/b] preview   [b]enter[/b] keep   [b]esc[/b] cancel", classes="hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        try:
            ol.highlighted = ol.get_option_index(self.original)
        except Exception:
            pass

    @on(OptionList.OptionHighlighted)
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self.app.theme = event.option.id

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.app.theme = self.original
        self.dismiss(None)


class LinkDialog(Dialog):
    """Shows a URL + scannable QR code (no browser needed — headless friendly)."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "cancel", "Close", show=False),
        Binding("y", "copy", "Copy"),
    ]

    def __init__(self, title: str, url: str):
        super().__init__()
        self.title_text = title
        self.url = url

    def compose(self) -> ComposeResult:
        with Vertical(classes="box link-box"):
            yield Label(self.title_text, classes="box-title")
            qr = qr_text(self.url)
            if qr is not None:
                yield Static(qr, classes="qr")
            yield Static(Text(self.url, style="bold underline"), classes="url")
            yield Static(
                "Scan with your phone or copy the link.\n[b]y[/b] copy to clipboard   [b]esc[/b] close",
                classes="hint",
            )

    def action_copy(self) -> None:
        self.app.copy_to_clipboard(self.url)
        self.notify("Copied (if your terminal supports OSC 52).")


class SettingsDialog(Dialog):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def compose(self) -> ComposeResult:
        cfg = self.app.config
        with Vertical(classes="box"):
            yield Label("⚙ Settings", classes="box-title")
            yield Static(f"Signed in as [b]@{cfg.login}[/b] (via {cfg.auth_method or 'token'})", classes="box-body")
            yield Static("Clone directory", classes="field-label")
            yield Input(value=cfg.clone_dir, id="clone_dir")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Change theme", id="theme")
                yield Button("Sign out", variant="error", id="signout")
            yield Static("[b]tab[/b] move   [b]enter[/b] press   [b]ctrl+s[/b] save   [b]esc[/b] close", classes="hint")

    @on(Input.Submitted)
    def action_save(self) -> None:
        value = self.query_one("#clone_dir", Input).value.strip()
        if value:
            self.app.config.clone_dir = value
            self.app.config.save()
            Path(self.app.config.clone_path).mkdir(parents=True, exist_ok=True)
        self.dismiss("saved")

    @on(Button.Pressed, "#save")
    def save_btn(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#theme")
    def theme_btn(self) -> None:
        self.dismiss("theme")

    @on(Button.Pressed, "#signout")
    def signout_btn(self) -> None:
        self.dismiss("signout")


# --------------------------------------------------------------------------
class GitRunDialog(Dialog):
    """Runs a git command, streaming its progress output live."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("enter", "close", "Close", show=False)]

    def __init__(self, title: str, args: list[str], cwd: str | Path | None, token: str | None):
        super().__init__()
        self.title_text = title
        self.args = args
        self.cwd = cwd
        self.token = token
        self.done = False
        self.code = -1

    def compose(self) -> ComposeResult:
        with Vertical(classes="box wide"):
            yield Label(self.title_text, classes="box-title")
            yield Static("[b]git " + " ".join(self.args) + "[/b]", classes="cmd", markup=True)
            yield Log(id="out", highlight=False)
            yield Static("⏳ working…", id="state", classes="hint")

    def on_mount(self) -> None:
        self.execute()

    @work(exclusive=True)
    async def execute(self) -> None:
        log = self.query_one(Log)
        last: list[str] = [""]

        def on_line(line: str) -> None:
            # progress lines like "Receiving objects:  42%" overwrite each other
            key = line.split(":")[0]
            if last[0] and key == last[0].split(":")[0] and "%" in line:
                lines = list(log.lines)
                if lines:
                    log.clear()
                    log.write_lines(lines[:-1])
            log.write_line(line)
            last[0] = line

        try:
            self.code = await gitops.stream(self.args, self.cwd, self.token, on_line)
        except Exception as exc:  # pragma: no cover
            log.write_line(f"error: {exc}")
            self.code = 1
        self.done = True
        state = self.query_one("#state", Static)
        if self.code == 0:
            state.update("[b green]✔ done[/]  —  press [b]enter[/b] to close")
            self.set_timer(0.8, self._auto_close)
        else:
            state.update(f"[b red]✘ failed (exit {self.code})[/]  —  press [b]enter[/b] to close")

    def _auto_close(self) -> None:
        if self.is_attached and self.app.screen is self:
            self.dismiss(True)

    def action_close(self) -> None:
        if self.done:
            self.dismiss(self.code == 0)
        else:
            self.notify("Still running… please wait.", severity="warning")

    def action_cancel(self) -> None:
        self.action_close()


class HelpDialog(Dialog):
    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("question_mark", "cancel", "Close", show=False),
        Binding("q", "cancel", "Close", show=False),
    ]

    def __init__(self, sections: list[tuple[str, list[tuple[str, str]]]]):
        super().__init__()
        self.sections = sections

    def compose(self) -> ComposeResult:
        text = Text()
        for title, keys in self.sections:
            text.append(f"\n {title}\n", style="bold")
            for key, desc in keys:
                text.append(f"   {key:<14}", style="bold #22D3EE")
                text.append(f"{desc}\n")
        with Vertical(classes="box wide"):
            yield Label("⌨ Keyboard shortcuts", classes="box-title")
            with VerticalScroll(classes="help-scroll"):
                yield Static(text)
            yield Static("[b]esc[/b] close", classes="hint")
