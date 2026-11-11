"""MCP server that lets Claude Code hand work to role-based models.

Tools:
  delegate(role, task, cwd, reasoning)  - run a role on a task, return its output
  list_roles(cwd)            - show current role -> model mapping and config sources
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .config import Config

mcp = FastMCP("roles")


def _exe(name: str, hint: str) -> str:
    # Use the resolved path: on Windows CLIs are .cmd shims that subprocess can't find by bare name.
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} CLI not found. {hint}")
    return path


# Reasoning levels each CLI accepts. A role sets one with `reasoning:` in roles.yaml.
REASONING_LEVELS = {
    "codex": ("minimal", "low", "medium", "high", "xhigh"),   # -c model_reasoning_effort=...
    "claude": ("low", "medium", "high", "xhigh", "max"),      # --effort ...
}
REASONING_LEVELS["deepseek"] = REASONING_LEVELS["claude"]


def check_reasoning(backend: str, level: str | None) -> str | None:
    if not level:
        return None
    level = str(level).strip().lower()
    allowed = REASONING_LEVELS.get(backend, ())
    if level not in allowed:
        raise ValueError(f"reasoning '{level}' is not valid for backend {backend}. Use one of: {', '.join(allowed)}")
    return level


# Short codex model names -> full slug. Used only when codex's own model cache can't be read.
CODEX_ALIASES_FALLBACK = {
    "astra": "gpt-6-astra",
    "sol": "gpt-6.1-sol",
    "terra": "gpt-6.1-terra",
    "luna": "gpt-6-luna",
}


def _codex_slugs() -> list[str]:
    """Model slugs from codex's cache (~/.codex/models_cache.json, or $CODEX_HOME). Empty if unreadable."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = data.get("models", []) if isinstance(data, dict) else data
    return [m["slug"] for m in models
            if isinstance(m, dict) and m.get("slug") and m.get("visibility") != "hide"]


def resolve_codex_model(model: str) -> str:
    """Expand a short name like 'sol' to the newest matching slug ('gpt-6.1-sol').
    Full slugs and unknown names pass through unchanged (codex reports bad names itself)."""
    name = model.strip().lower()
    if not re.fullmatch(r"[a-z]+", name):
        return model
    best: tuple[tuple[int, ...], str] | None = None
    for slug in _codex_slugs():
        if slug == name:
            return slug
        m = re.fullmatch(rf"gpt-(\d+(?:\.\d+)*)-{name}", slug)
        if m:
            version = tuple(int(p) for p in m.group(1).split("."))
            if best is None or version > best[0]:
                best = (version, slug)
    if best:
        return best[1]
    return CODEX_ALIASES_FALLBACK.get(name, model)


def resolve_model(backend: str, model: str) -> str:
    return resolve_codex_model(model) if backend == "codex" else model


def build_command(backend: str, model: str, cfg: Config, reasoning: str | None = None) -> tuple[list[str], dict]:
    """Build the child command. The prompt is NOT in argv: it goes to the child's stdin
    (avoids the ~32K Windows command-line limit and stops codex waiting on an open stdin)."""
    env = os.environ.copy()
    reasoning = check_reasoning(backend, reasoning)

    if backend == "codex":
        model = resolve_codex_model(model)
        exe = _exe("codex", "Install: npm i -g @openai/codex, then `codex login`.")
        cmd = [
            exe, "exec", "-m", model,
            "--sandbox", "workspace-write",
            # Nobody can answer an approval prompt from inside an MCP call; without this, a sandbox
            # escalation request blocks forever.
            "-c", 'approval_policy="never"',
            "--skip-git-repo-check",
            "--color", "never",
        ]
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        cmd.append("-")  # read the prompt from stdin
        return cmd, env

    if backend in ("claude", "deepseek"):
        exe = _exe("claude", "Install Claude Code first.")
        cmd = [exe, "-p", "--model", model, "--permission-mode", "acceptEdits"]
        if reasoning:
            cmd += ["--effort", reasoning]
        if backend == "deepseek":
            key, where = cfg.api_key("deepseek")
            if not key:
                raise RuntimeError(f"DeepSeek API key {where}.")
            base_url = cfg.backend_value("deepseek", "base_url") or "https://api.deepseek.com/anthropic"
            # Only this child process talks to DeepSeek; your main session stays on the subscription.
            env["ANTHROPIC_BASE_URL"] = base_url
            env["ANTHROPIC_AUTH_TOKEN"] = key
            env.pop("ANTHROPIC_API_KEY", None)
            for tier in ("OPUS", "SONNET", "HAIKU"):
                env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = model
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
        return cmd, env

    raise ValueError(f"Unknown backend: {backend}")


def model_label(backend: str, model: str) -> str:
    """'sol -> gpt-6.1-sol' when a short name was expanded, else just the model."""
    full = resolve_model(backend, model)
    return f"{model} -> {full}" if full != model else model


def describe(r: dict) -> str:
    label = model_label(r.get("backend", ""), str(r.get("model", "")))
    return f"{r.get('backend')} / {label}" + (f" / reasoning={r['reasoning']}" if r.get("reasoning") else "")


@mcp.tool()
def list_roles(cwd: str = "") -> str:
    """Show which backend and model each role uses right now, and where config came from.

    cwd: absolute path of the current project (a ./roles.yaml there overrides the global one)
    """
    cfg = Config(cwd or None)
    lines = [f"{name}: {describe(r)}" for name, r in cfg.roles.items()]
    lines.append(f"\nroles file: {cfg.roles_file}")
    lines.append(f"properties: {cfg.properties_file or '(none)'}")
    if any(r.get("backend") == "deepseek" for r in cfg.roles.values()):
        key, where = cfg.api_key("deepseek")
        lines.append(f"deepseek key: {'found in ' + where if key else where}")
    return "\n".join(lines)


@mcp.tool()
def delegate(role: str, task: str, cwd: str = ".", reasoning: str = "") -> str:
    """Hand a task to a role (planner, executor, reviewer, or any role in roles.yaml).

    role: role name from roles.yaml
    task: full, self-contained task description (include the plan when calling executor/reviewer)
    cwd:  absolute path of the project folder the model should work in
    reasoning: optional one-off override of the role's reasoning level
               (codex: minimal|low|medium|high|xhigh; claude/deepseek: low|medium|high|xhigh|max)
    """
    cwd = os.path.abspath(cwd)
    cfg = Config(cwd)  # re-read every call, so edits apply without restarting
    roles = cfg.roles
    if role not in roles:
        return f"Unknown role '{role}'. Available: {', '.join(roles)}"
    r = roles[role]
    prompt = f"{r.get('instructions', '').strip()}\n\n# Task\n{task}"
    level = reasoning or r.get("reasoning")
    try:
        cmd, env = build_command(r["backend"], r["model"], cfg, level)
        proc = subprocess.run(
            # The prompt goes in via stdin, which is then closed. Never let the child inherit the
            # MCP stdio channel: codex would block reading it as extra prompt input.
            cmd, cwd=cwd, env=env, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=cfg.timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[{role}] timed out."
    except ValueError as e:
        return f"[{role}] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[{role}] failed to start: {e}"

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        out += f"\n\n[exit code {proc.returncode}]\n{(proc.stderr or '').strip()[-3000:]}"
    return f"[{role} -> {r['backend']}/{resolve_model(r['backend'], r['model'])}]\n{out[-60000:]}"


def run() -> None:
    mcp.run()
