"""Persistent configuration for gitui.

Stored at ``$XDG_CONFIG_HOME/gitui/config.json`` (normally ``~/.config/gitui``)
with permissions 0600 because it contains your GitHub token.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Public OAuth App client ID used for "Sign in with browser" (device flow).
# Client IDs are NOT secret. Leave empty to require the user to supply one
# (see README -> "Enabling browser sign-in"), or set GITUI_CLIENT_ID.
DEFAULT_CLIENT_ID = ""

OAUTH_SCOPES = "repo read:org workflow delete_repo gist"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "gitui"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    token: str = ""
    login: str = ""
    theme: str = "gitui"
    clone_dir: str = str(Path.home() / "github")
    client_id: str = ""
    auth_method: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def effective_client_id(self) -> str:
        return os.environ.get("GITUI_CLIENT_ID") or self.client_id or DEFAULT_CLIENT_ID

    @property
    def clone_path(self) -> Path:
        return Path(os.path.expanduser(self.clone_dir))

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)

    def sign_out(self) -> None:
        self.token = ""
        self.login = ""
        self.auth_method = ""
        self.save()
