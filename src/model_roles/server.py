"""MCP server that lets Claude Code hand work to role-based models.

Tools:
  delegate(role, task, cwd, reasoning, attach, save_as)  - run a role on a task
  list_roles(cwd)            - show current role -> model mapping and config sources
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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


# Permission settings a role may set in roles.yaml.
PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")  # claude/deepseek
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")      # codex


def check_permission_mode(mode: str | None) -> str | None:
    if mode is None:
        return None
    if isinstance(mode, str):
        mode = mode.strip()
        if not mode:
            return None
    if not isinstance(mode, str) or mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode '{mode}' is not valid. Use one of: {', '.join(PERMISSION_MODES)}")
    return mode


def check_sandbox(mode: str | None) -> str | None:
    if mode is None:
        return None
    if isinstance(mode, str):
        mode = mode.strip().lower()
        if not mode:
            return None
    if not isinstance(mode, str) or mode not in SANDBOX_MODES:
        raise ValueError(
            f"sandbox '{mode}' is not valid. Use one of: {', '.join(SANDBOX_MODES)}")
    return mode


def str_list(value, key: str) -> list[str]:
    """A roles.yaml value that may be a single string or a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{key} must be a string or a list of strings")
    out = [v.strip() for v in value]
    return [v for v in out if v]


def allowed_tools_list(value) -> list[str]:
    """roles.yaml `allowed_tools` (claude/deepseek)."""
    return str_list(value, "allowed_tools")


def role_timeout(r: dict, default: int) -> int:
    """roles.yaml per-role `timeout_seconds`, else the global one."""
    value = r.get("timeout_seconds")
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"timeout_seconds must be a positive whole number, got {value!r}")
    return value


def writable_dirs_list(value) -> list[str]:
    """roles.yaml `writable_dirs` (codex): extra folders the sandbox may write, `~` expanded."""
    return [str(Path(d).expanduser()) for d in str_list(value, "writable_dirs")]


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


def build_command(backend: str, model: str, cfg: Config, reasoning: str | None = None, role: dict | None = None) -> tuple[list[str], dict]:
    """Build the child command. The prompt is NOT in argv: it goes to the child's stdin
    (avoids the ~32K Windows command-line limit and stops codex waiting on an open stdin)."""
    env = os.environ.copy()
    reasoning = check_reasoning(backend, reasoning)
    role = role or {}

    if backend == "codex":
        model = resolve_codex_model(model)
        exe = _exe("codex", "Install: npm i -g @openai/codex, then `codex login`.")
        sandbox = check_sandbox(role.get("sandbox")) or "workspace-write"
        cmd = [
            exe, "exec", "-m", model,
            "--sandbox", sandbox,
            # Nobody can answer an approval prompt from inside an MCP call; without this, a sandbox
            # escalation request blocks forever.
            "-c", 'approval_policy="never"',
            "--skip-git-repo-check",
            "--color", "never",
        ]
        if role.get("network"):
            cmd += ["-c", "sandbox_workspace_write.network_access=true"]
        for d in writable_dirs_list(role.get("writable_dirs")):
            cmd += ["--add-dir", d]
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        cmd.append("-")  # read the prompt from stdin
        return cmd, env

    if backend in ("claude", "deepseek"):
        exe = _exe("claude", "Install Claude Code first.")
        mode = check_permission_mode(role.get("permission_mode")) or "acceptEdits"
        cmd = [exe, "-p", "--model", model, "--permission-mode", mode]
        tools = allowed_tools_list(role.get("allowed_tools"))
        if tools:
            cmd += ["--allowedTools", *tools]
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
    backend = r.get("backend", "")
    parts = [f"{backend} / {model_label(backend, str(r.get('model', '')))}"]
    if r.get("reasoning"):
        parts.append(f"reasoning={r['reasoning']}")
    if r.get("timeout_seconds") is not None:
        parts.append(f"timeout={r['timeout_seconds']}s")
    if backend == "codex":
        if r.get("sandbox"):
            parts.append(f"sandbox={r['sandbox']}")
        if r.get("network"):
            parts.append("network")
        dirs = r.get("writable_dirs")
        if dirs:
            try:
                parts.append("dirs=" + ",".join(str_list(dirs, "writable_dirs")))
            except ValueError:
                parts.append("dirs=?")
    elif backend in ("claude", "deepseek"):
        if r.get("permission_mode"):
            parts.append(f"permissions={r['permission_mode']}")
        tools = r.get("allowed_tools")
        if tools:
            try:
                parts.append("tools=" + ",".join(allowed_tools_list(tools)))
            except ValueError:
                parts.append("tools=?")
    return " / ".join(parts)


@mcp.tool()
def list_roles(cwd: str = "") -> str:
    """Show which backend and model each role uses right now, and where config came from.

    cwd: absolute path of the current project (a ./roles.yaml there overrides the global one)
    """
    cfg = Config(cwd or None)
    lines = [f"{name}: {describe(r)}" for name, r in cfg.roles.items()]
    limits = cfg.pipeline_limits()
    lines.append("\npipeline limits: " + " ".join(f"{k}={v}" for k, v in limits.items()))
    lines.append(f"roles file: {cfg.roles_file}")
    lines.append(f"properties: {cfg.properties_file or '(none)'}")
    if any(r.get("backend") == "deepseek" for r in cfg.roles.values()):
        key, where = cfg.api_key("deepseek")
        lines.append(f"deepseek key: {'found in ' + where if key else where}")
    return "\n".join(lines)


# Role outputs are kept here so the orchestrating session can pass them between roles by
# name instead of re-typing them (re-typing a plan into every call costs output tokens).
RUN_DIR = ".model-roles"
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def run_dir(cwd: str) -> Path:
    d = Path(cwd) / RUN_DIR
    d.mkdir(exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():  # keep run files out of git status / the reviewer's diff
        ignore.write_text("*\n", encoding="utf-8")
    return d


def artifact_path(cwd: str, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"bad artifact name '{name}': use letters, digits, _ or -")
    return run_dir(cwd) / f"{name}.md"


# Lines worth showing the orchestrator: step ids / status rows, verdicts, section headings.
DIGEST_LINE = re.compile(
    r"^\W*(S\d+\b|MISSING|UNPLANNED|#|Verification commands|Definition of done|Review responses|"
    r"PLAN_OK|PLAN_ISSUES|APPROVE|CHANGES_NEEDED|TESTS_PASS|TESTS_FAIL|BLOCKED|FAIL\b)"
    r"|.*\[(BLOCKER|MAJOR)\]")


def digest(text: str, tail_lines: int = 25, limit: int = 6000) -> str:
    """Short view of a long output: key lines from the body plus the last lines."""
    lines = text.splitlines()
    if len(text) <= limit:
        return text
    head, tail = lines[:-tail_lines], lines[-tail_lines:]
    keys = [ln for ln in head if DIGEST_LINE.match(ln)]
    out = "\n".join(keys + ["[...]"] + tail)
    return out if len(out) <= limit else "[...]\n" + out[-limit:]


def git_snapshot(cwd: str) -> tuple[str, ...] | None:
    """Fingerprint of the working tree (status, tracked diff, untracked contents). None if not git."""
    def git(*args: str, stdin: str | None = None) -> str:
        # Never inherit the server's stdin (the MCP pipe): on Windows a child that shares it
        # blocks at startup while the server is waiting to read its next message.
        return subprocess.run(["git", *args], cwd=cwd, input=stdin,
                              stdin=None if stdin is not None else subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=True, timeout=120).stdout
    try:
        diff = git("diff", "--no-ext-diff", "--binary")
        untracked = git("ls-files", "-o", "--exclude-standard", "-z")
        files = [f for f in untracked.split("\0") if f]
        return (
            git("status", "--short"),
            git("hash-object", "--stdin", stdin=diff),
            git("hash-object", "--stdin-paths", stdin="\n".join(files)) if files else "",
        )
    except (OSError, subprocess.SubprocessError):
        return None


@mcp.tool()
def delegate(role: str, task: str, cwd: str = ".", reasoning: str = "",
             attach: list[str] | None = None, save_as: str = "") -> str:
    """Hand a task to a role (planner, plan_reviewer, executor, tester, reviewer, or any role in roles.yaml).

    role: role name from roles.yaml
    task: the instruction for this call. Do NOT paste earlier outputs here - use `attach`.
    cwd:  absolute path of the project folder the model should work in
    reasoning: optional one-off override of the role's reasoning level
               (codex: minimal|low|medium|high|xhigh; claude/deepseek: low|medium|high|xhigh|max)
    attach: names of earlier saved outputs (e.g. ["plan", "exec"]) to include in full in the
            child's prompt. The server inserts them; you never re-type them.
    save_as: name to save this call's full output under (e.g. "plan"). When set, you get back a
             short digest (step/status lines, verdict, last lines) plus the file path instead of
             the full text; Read the file only if you really need more.
    """
    cwd = os.path.abspath(cwd)
    cfg = Config(cwd)  # re-read every call, so edits apply without restarting
    roles = cfg.roles
    if role not in roles:
        return f"Unknown role '{role}'. Available: {', '.join(roles)}"
    r = roles[role]
    try:
        parts = [r.get("instructions", "").strip(), f"# Task\n{task}"]
        for name in attach or []:
            path = artifact_path(cwd, name)
            if not path.exists():
                return f"[{role}] nothing saved as '{name}' yet (looked for {path})."
            parts.append(f"# Attached: {name}\n{path.read_text(encoding='utf-8').strip()}")
        out_file = artifact_path(cwd, save_as) if save_as else None
    except ValueError as e:
        return f"[{role}] {e}"
    prompt = "\n\n".join(parts)
    level = reasoning or r.get("reasoning")
    check_unchanged = bool(r.get("read_only_check"))
    before = git_snapshot(cwd) if check_unchanged else None
    last_msg = None
    timeout = cfg.timeout
    try:
        timeout = role_timeout(r, cfg.timeout)
        cmd, env = build_command(r["backend"], r["model"], cfg, level, r)
        if r["backend"] == "codex":
            # Only the final answer, not codex's progress transcript.
            fd, last_msg = tempfile.mkstemp(prefix="codex-last-", suffix=".txt")
            os.close(fd)
            cmd[-1:-1] = ["--output-last-message", last_msg]
        proc = subprocess.run(
            # The prompt goes in via stdin, which is then closed. Never let the child inherit the
            # MCP stdio channel: codex would block reading it as extra prompt input.
            cmd, cwd=cwd, env=env, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        if last_msg:
            final = Path(last_msg).read_text(encoding="utf-8", errors="replace").strip()
            out = final or out
    except subprocess.TimeoutExpired:
        return f"[{role}] timed out after {timeout}s."
    except ValueError as e:
        return f"[{role}] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[{role}] failed to start: {e}"
    finally:
        if last_msg:
            Path(last_msg).unlink(missing_ok=True)

    if proc.returncode != 0:
        out += f"\n\n[exit code {proc.returncode}]\n{(proc.stderr or '').strip()[-3000:]}"
    if check_unchanged and before is not None and git_snapshot(cwd) != before:
        out += ("\n\n[WARNING: the working tree changed during this read-only role's run - "
                "treat its results as untrusted and tell the user and the reviewer.]")
    header = f"[{role} -> {r['backend']}/{resolve_model(r['backend'], r['model'])}]"
    if out_file:
        out_file.write_text(out + "\n", encoding="utf-8")
        return f"{header} saved full output ({len(out)} chars) to {out_file}\n{digest(out)}"
    return f"{header}\n{out[-60000:]}"


def run() -> None:
    mcp.run()
