"""`model-roles` command.

  model-roles                 run the MCP server (stdio) - what Claude Code launches
  model-roles serve           same
  model-roles init [--force]  write roles.yaml + model-roles.properties to the config dir
                              and install the /pipeline slash command into ~/.claude/commands
  model-roles doctor [DIR]    show which config files and keys would be used
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import PROPERTIES_NAME, Config, bundled, config_dir
from .server import check_reasoning, describe

PROPERTIES_TEMPLATE = """\
# model-roles secrets/overrides. Keep this file out of git.
# Precedence for keys: environment (incl. MCP server env) > this file > roles.yaml.
deepseek.api_key=
# deepseek.base_url=https://api.deepseek.com/anthropic
# timeout_seconds=1800
"""


def _copy(src: Path, dst: Path, force: bool) -> None:
    if dst.exists() and not force:
        print(f"  exists, kept: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"  wrote: {dst}")


def cmd_init(force: bool) -> None:
    d = config_dir()
    print(f"config dir: {d}")
    _copy(bundled("roles.yaml"), d / "roles.yaml", force)
    props = d / PROPERTIES_NAME
    if props.exists() and not force:
        print(f"  exists, kept: {props}")
    else:
        props.parent.mkdir(parents=True, exist_ok=True)
        props.write_text(PROPERTIES_TEMPLATE, encoding="utf-8")
        print(f"  wrote: {props}")
    _copy(bundled("pipeline.md"), Path.home() / ".claude" / "commands" / "pipeline.md", force)
    print("\nNext: register the server with Claude Code, e.g.\n"
          "  claude mcp add roles --scope user -e DEEPSEEK_API_KEY=sk-... -- model-roles")


def cmd_doctor(project_dir: str | None) -> None:
    cfg = Config(project_dir)
    print(f"roles file : {cfg.roles_file}")
    print(f"properties : {cfg.properties_file or '(none)'}")
    for name, r in cfg.roles.items():
        print(f"  {name}: {describe(r)}")
        try:
            check_reasoning(r.get("backend"), r.get("reasoning"))
        except ValueError as e:
            print(f"    WARNING: {e}")
    backends = {r.get("backend") for r in cfg.roles.values()}
    for b in sorted(backends & {"deepseek"}):
        key, where = cfg.api_key(b)
        shown = f"{key[:5]}...{key[-4:]} from {where}" if key and len(key) > 12 else (
            f"set from {where}" if key else where)
        print(f"{b} key: {shown}")
    for exe in ("claude", "codex"):
        print(f"{exe:6} CLI: {shutil.which(exe) or 'NOT FOUND'}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="model-roles", description="Role-based model delegation MCP server")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")
    i = sub.add_parser("init", help="create config files and install the /pipeline command")
    i.add_argument("--force", action="store_true", help="overwrite existing files")
    d = sub.add_parser("doctor", help="show resolved config and key sources")
    d.add_argument("project_dir", nargs="?", default=None)
    a = p.parse_args(argv)

    if a.cmd in (None, "serve"):
        from .server import run
        run()
    elif a.cmd == "init":
        cmd_init(a.force)
    elif a.cmd == "doctor":
        cmd_doctor(a.project_dir)


if __name__ == "__main__":
    sys.exit(main())
