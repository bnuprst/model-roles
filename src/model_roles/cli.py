"""`model-roles` command.

  model-roles                 run the MCP server (stdio) - what Claude Code launches
  model-roles serve           same
  model-roles init [--force] [--no-path]
                              write roles.yaml + model-roles.properties to the config dir,
                              install the /pipeline slash command into ~/.claude/commands,
                              and add the folder holding the `model-roles` script to PATH
                              (if missing; run as `python -m model_roles init` in that case)
  model-roles doctor [DIR]    show which config files and keys would be used
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import sysconfig
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


def _script_name() -> str:
    return "model-roles.exe" if os.name == "nt" else "model-roles"


def scripts_dir() -> Path | None:
    """Folder that holds the installed `model-roles` script."""
    candidates = []
    argv0 = Path(sys.argv[0])
    if argv0.stem == "model-roles":
        candidates.append(argv0.resolve().parent)
    schemes = [sysconfig.get_default_scheme()]
    try:
        schemes.append(sysconfig.get_preferred_scheme("user"))
    except AttributeError:  # very old Python
        schemes.append(f"{os.name}_user")
    for scheme in schemes:
        try:
            candidates.append(Path(sysconfig.get_path("scripts", scheme)))
        except KeyError:
            pass
    for c in candidates:
        if (c / _script_name()).exists():
            return c
    return None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(p.strip().strip('"'))))


def on_path(folder: Path) -> bool:
    return _norm(str(folder)) in {_norm(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}


def _add_to_user_path_windows(folder: Path) -> bool:
    """Append folder to HKCU\\Environment\\Path. Returns True if it changed."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in current.split(";") if p]
        if _norm(str(folder)) in {_norm(p) for p in parts}:
            return False
        parts.append(str(folder))
        winreg.SetValueEx(key, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
                          else winreg.REG_EXPAND_SZ, ";".join(parts))
    # Tell Explorer etc. that the environment changed, so new terminals pick it up.
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000,
        ctypes.byref(ctypes.c_ulong()))
    return True


def ensure_path() -> None:
    folder = scripts_dir()
    if folder is None:
        print("PATH: could not find the installed model-roles script; skipped")
        return
    if on_path(folder):
        print(f"PATH: ok ({folder})")
        return
    if os.name == "nt":
        try:
            changed = _add_to_user_path_windows(folder)
        except OSError as e:
            print(f"PATH: could not update user PATH ({e}). Add this folder by hand: {folder}")
            return
        if changed:
            print(f"PATH: added {folder} to your user PATH.\n"
                  "      Open a new terminal (and restart VS Code / Claude Code) to pick it up.")
        else:
            print(f"PATH: {folder} is already in your user PATH; open a new terminal to pick it up.")
    else:
        print(f"PATH: {folder} is not on PATH. Add this to your shell profile:\n"
              f'      export PATH="{folder}:$PATH"')


def cmd_init(force: bool, fix_path: bool = True) -> None:
    d = config_dir()
    print(f"config dir: {d}")
    _copy(bundled("roles.yaml"), d / "roles.yaml", force)
    props = d / PROPERTIES_NAME
    if props.exists():  # holds API keys: never overwritten, even with --force
        print(f"  exists, kept: {props}")
    else:
        props.parent.mkdir(parents=True, exist_ok=True)
        props.write_text(PROPERTIES_TEMPLATE, encoding="utf-8")
        print(f"  wrote: {props}")
    _copy(bundled("pipeline.md"), Path.home() / ".claude" / "commands" / "pipeline.md", force)
    if fix_path:
        ensure_path()
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
    folder = scripts_dir()
    if shutil.which("model-roles"):
        print(f"model-roles on PATH: yes ({shutil.which('model-roles')})")
    else:
        print(f"model-roles on PATH: NO - run `python -m model_roles init` to add {folder or 'its folder'}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="model-roles", description="Role-based model delegation MCP server")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")
    i = sub.add_parser("init", help="create config files and install the /pipeline command")
    i.add_argument("--force", action="store_true", help="overwrite existing files")
    i.add_argument("--no-path", action="store_true", help="do not touch PATH")
    d = sub.add_parser("doctor", help="show resolved config and key sources")
    d.add_argument("project_dir", nargs="?", default=None)
    a = p.parse_args(argv)

    if a.cmd in (None, "serve"):
        from .server import run
        run()
    elif a.cmd == "init":
        cmd_init(a.force, fix_path=not a.no_path)
    elif a.cmd == "doctor":
        cmd_doctor(a.project_dir)


if __name__ == "__main__":
    sys.exit(main())
