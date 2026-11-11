"""Config discovery and secret resolution.

roles.yaml lookup (first hit wins):
  1. $MODEL_ROLES_CONFIG (or legacy $ROLES_FILE)
  2. ./roles.yaml in the project the tool is called for (cwd argument)
  3. <config dir>/roles.yaml        (created by `model-roles init`)
  4. the default roles.yaml bundled with the package

<config dir> is $MODEL_ROLES_HOME, else ~/.config/model-roles.

Backend secrets (e.g. the DeepSeek API key), first non-empty wins:
  1. environment variable named by `api_key_env` (default <BACKEND>_API_KEY).
     This is how keys set in MCP server settings arrive:
     `claude mcp add roles -e DEEPSEEK_API_KEY=sk-... -- model-roles`
  2. properties file: `deepseek.api_key=sk-...`
       $MODEL_ROLES_PROPERTIES, else model-roles.properties next to the
       active roles.yaml, else <config dir>/model-roles.properties
  3. roles.yaml: backends.deepseek.api_key
"""
from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

import yaml

PROPERTIES_NAME = "model-roles.properties"


def config_dir() -> Path:
    home = os.environ.get("MODEL_ROLES_HOME")
    return Path(home).expanduser() if home else Path.home() / ".config" / "model-roles"


def bundled(name: str) -> Path:
    return Path(str(resources.files("model_roles") / "data" / name))


def find_roles_file(project_dir: str | None = None) -> Path:
    for var in ("MODEL_ROLES_CONFIG", "ROLES_FILE"):
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser()
    candidates = []
    if project_dir:
        candidates.append(Path(project_dir) / "roles.yaml")
    candidates.append(config_dir() / "roles.yaml")
    for c in candidates:
        if c.is_file():
            return c
    return bundled("roles.yaml")


def find_properties_file(roles_file: Path) -> Path | None:
    if os.environ.get("MODEL_ROLES_PROPERTIES"):
        p = Path(os.environ["MODEL_ROLES_PROPERTIES"]).expanduser()
        return p if p.is_file() else None
    for c in (roles_file.with_name(PROPERTIES_NAME), config_dir() / PROPERTIES_NAME):
        if c.is_file():
            return c
    return None


def parse_properties(text: str) -> dict[str, str]:
    """Minimal Java .properties parser: `key=value`, `key: value`, `#`/`!` comments,
    trailing-backslash line continuation."""
    out: dict[str, str] = {}
    logical, buf = [], ""
    for raw in text.splitlines():
        line = raw.lstrip()
        if not buf and (not line or line[0] in "#!"):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        logical.append(buf + line)
        buf = ""
    if buf:
        logical.append(buf)
    for line in logical:
        idx = min((i for i in (line.find("="), line.find(":")) if i >= 0), default=-1)
        key, val = (line[:idx], line[idx + 1:]) if idx >= 0 else (line, "")
        out[key.strip()] = val.strip()
    return out


class Config:
    def __init__(self, project_dir: str | None = None):
        self.roles_file = find_roles_file(project_dir)
        with open(self.roles_file, encoding="utf-8") as f:
            self.data: dict = yaml.safe_load(f) or {}
        self.properties_file = find_properties_file(self.roles_file)
        self.props = (
            parse_properties(self.properties_file.read_text(encoding="utf-8"))
            if self.properties_file else {}
        )

    @property
    def roles(self) -> dict:
        return self.data.get("roles", {})

    @property
    def timeout(self) -> int:
        return int(self.props.get("timeout_seconds") or self.data.get("timeout_seconds", 1800))

    def backend(self, name: str) -> dict:
        return (self.data.get("backends") or {}).get(name) or {}

    def backend_value(self, backend: str, key: str) -> str | None:
        """Property file overrides yaml for any backend setting (e.g. deepseek.base_url)."""
        for k in (f"{backend}.{key}", f"backends.{backend}.{key}"):
            if self.props.get(k):
                return self.props[k]
        v = self.backend(backend).get(key)
        return str(v) if v else None

    def api_key(self, backend: str) -> tuple[str | None, str]:
        """Return (key, where it came from)."""
        env_name = self.backend(backend).get("api_key_env") or f"{backend.upper()}_API_KEY"
        if os.environ.get(env_name):
            return os.environ[env_name], f"env {env_name}"
        for k in (f"{backend}.api_key", f"backends.{backend}.api_key"):
            if self.props.get(k):
                return self.props[k], f"properties {self.properties_file}"
        v = self.backend(backend).get("api_key")
        if v:
            return str(v), f"yaml {self.roles_file}"
        return None, (
            f"not set (env {env_name}, '{backend}.api_key' in {PROPERTIES_NAME}, "
            f"or backends.{backend}.api_key in roles.yaml)"
        )
