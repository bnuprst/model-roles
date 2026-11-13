# Model roles for Claude Code

Your main Claude Code session (Claude subscription, Opus or Fable) is the boss.
It hands work to other models by role. `roles.yaml` decides which model does which role.

| Role | Default | Paid by |
|---|---|---|
| planner | Opus (medium reasoning) via headless Claude Code | Claude subscription |
| plan_reviewer | GPT Terra via `codex exec` | ChatGPT/Codex subscription |
| executor | GPT-6 Luna (`luna`, reasoning `high`) via `codex exec` | ChatGPT/Codex login |
| tester | Haiku via headless Claude Code | Claude subscription |
| reviewer | GPT Sol via `codex exec` | ChatGPT/Codex subscription |

## Install (global)

Pick one. Each puts a `model-roles` command on your PATH.

```bash
uv tool install /path/to/model-roles      # recommended
pipx install /path/to/model-roles
pip install --user /path/to/model-roles   # plain pip
```

`pip install --user` can't change PATH on its own, so you may see "model-roles.exe is installed in ...
which is not on PATH". That's fine. Run `python -m model_roles init` once. `init` finds the folder the
script was installed to and, on Windows, adds it to your user PATH. On macOS and Linux it prints the
`export PATH=...` line to put in your shell profile. Then open a new terminal, and restart VS Code or
Claude Code so they see the new PATH. To leave PATH alone, use `init --no-path`. `model-roles doctor`
tells you whether the command is on PATH.

Then:

```bash
npm i -g @openai/codex && codex login     # for the codex backend
model-roles init                          # writes ~/.config/model-roles/{roles.yaml,model-roles.properties}
                                          # and ~/.claude/commands/pipeline.md, and fixes PATH if needed
                                          # (use `python -m model_roles init` if the command isn't found yet)
claude mcp add roles --scope user -e DEEPSEEK_API_KEY=sk-... -- model-roles
model-roles doctor                        # shows which config and key will be used
```

Long tasks need a longer MCP timeout. Start Claude Code with, e.g.
`MCP_TOOL_TIMEOUT=1800000 claude`.

## DeepSeek API key

Set it in **one** of these places. The first one that has a value wins:

1. **MCP server settings (env).** This is the server's `env` block in Claude Code's MCP config.
   - Set it when you add the server: `claude mcp add roles -s user -e DEEPSEEK_API_KEY=sk-... -- model-roles`
   - Or edit the `env` of the `roles` server in `~/.claude.json` (user scope) or `.mcp.json` (project scope):
     ```json
     "roles": { "command": "model-roles", "args": [], "env": { "DEEPSEEK_API_KEY": "sk-..." } }
     ```
   - A `DEEPSEEK_API_KEY` exported in your shell works too.

   After you change it, open `/mcp` in Claude Code and reconnect the `roles` server. `/mcp` shows and reconnects servers; it does not edit env values.
2. **Properties file:** `model-roles.properties`
   ```properties
   deepseek.api_key=sk-...
   # deepseek.base_url=https://api.deepseek.com/anthropic
   # timeout_seconds=1800
   ```
   The file is looked up in this order: `$MODEL_ROLES_PROPERTIES`, then next to the active `roles.yaml`, then `~/.config/model-roles/`.
3. **YAML:** in `roles.yaml`
   ```yaml
   backends:
     deepseek:
       api_key: sk-...
   ```

To read the key from a different env var, set `backends.deepseek.api_key_env`.

## Where roles.yaml comes from

The first one found is used:

1. `$MODEL_ROLES_CONFIG` (`$ROLES_FILE` also works, for older setups)
2. `./roles.yaml` in the project folder passed as `cwd`, so each project can have its own roles
3. `~/.config/model-roles/roles.yaml` (set `$MODEL_ROLES_HOME` to move this folder)
4. the default file that ships with the package

Every tool call reads the config again, so edits apply without a restart.

## Use

```
/pipeline add retry with backoff to the payment client, with tests
```
Or just ask: "use the roles tool, ask the planner for ...".

The `/pipeline` stages: plan → plan review → execute → gap re-send of unfinished
steps → test → review → fix loops.

Reviewers tag each finding `BLOCKER`, `MAJOR` or `MINOR`; only BLOCKER/MAJOR fail a
review (`PLAN_ISSUES` / `CHANGES_NEEDED`). From the second round on, the reviewer gets
its previous review attached and checks those findings first instead of reviewing
from scratch, and the planner answers each finding (FIXED / REJECTED with a reason),
so rounds converge instead of turning up a fresh batch of nits each time.
After the first round only a BLOCKER triggers another full replan: open MAJORs
are attached to the executor (and the reviewer) as notes, because each replan of a
large plan tends to add new mechanisms that the next round then finds MAJORs in.

Loop limits live in `roles.yaml` (or `pipeline.<key>=` in `model-roles.properties`):

```yaml
pipeline:
  plan_review_rounds: 3   # then it asks you
  plan_major_rounds: 1    # rounds where a MAJOR forces a replan; later MAJORs go
                          # to the executor as notes, only BLOCKERs replan
  fix_loops: 3            # review -> fix -> test -> review
  exec_retries: 1         # re-sends of unfinished plan steps
```

## Swap models

Edit `roles.yaml`:

```yaml
planner:  { backend: claude,   model: opus }
executor: { backend: codex,    model: gpt-6-astra }
reviewer: { backend: deepseek, model: deepseek-v4-pro }
```

`tester` ships by default; you can add more (e.g. `security-reviewer`). They show up automatically.

`list_roles` and `model-roles doctor` print non-default permission settings (e.g. `claude / haiku / tools=Bash`).

For the codex backend you can use short names: `astra`, `sol`, `terra`, `luna`. Each one expands to the newest matching model in codex's local model cache (`~/.codex/models_cache.json`), so `sol` becomes `gpt-6.1-sol`. Write the full slug (`gpt-6-sol`) to pin a version. `list_roles` shows what each short name resolves to.

## Upgrading

Existing installs keep their old `roles.yaml`. Either copy the `plan_reviewer`,
`tester` and `pipeline` blocks and the updated `instructions` of `planner`,
`plan_reviewer`, `executor` and `reviewer` from the bundled `roles.yaml` into your `~/.config/model-roles/roles.yaml`
(or project `./roles.yaml`), or run `model-roles init --force` (this overwrites your
`roles.yaml` and pipeline command - back up custom edits first). `/pipeline` falls back
to `reviewer` if those roles are missing.

## Notes
- The DeepSeek override applies only to the child process. Your main session stays on the subscription.
- By default, claude/deepseek run `--permission-mode acceptEdits`, which auto-approves file edits; any command that would need approval (e.g. `./gradlew`, `npm`, `pytest`) is denied in headless mode. Codex runs `--sandbox workspace-write`: no network; writes limited to the project and temp dirs (so `~/.gradle` is not writable).
- Per role, claude/deepseek accept `allowed_tools` (a string or list of strings) and `permission_mode` (`default`, `acceptEdits`, `plan`, or `bypassPermissions`). Codex accepts `sandbox` (`read-only`, `workspace-write`, or `danger-full-access`), `network` (`true` to enable network access) and `writable_dirs` (extra writable folders, passed as `--add-dir`; `~` is expanded).
- For Gradle, give claude/deepseek `allowed_tools: [Bash]` (or `["Bash(./gradlew:*)"]`). For codex, keep `sandbox: workspace-write` and set `network: true` plus `writable_dirs: [~/.gradle]` (or your `GRADLE_USER_HOME`). Avoid `danger-full-access`: it turns the sandbox off entirely.
- The tester and the two review roles are told not to edit files, but the backends still run with write permission. Roles with `read_only_check: true` (on by default for those three) get a git working-tree fingerprint before and after the call, and the output carries a WARNING if anything changed. Use git and commit before each run.
- `delegate` takes `save_as` (store the role's full output as `.model-roles/<name>.md` in the project, git-ignored automatically) and `attach` (insert earlier saved outputs into the child's prompt). With `save_as`, the calling session gets only a short digest back. `/pipeline` uses this so the main session never re-types the plan, test log or review: that re-typing was most of its token cost.
- Codex roles return only codex's final message (`--output-last-message`), not its progress log.
- Existing installs keep their old `roles.yaml`: copy the `allowed_tools` lines into `~/.config/model-roles/roles.yaml` (or `./roles.yaml`), or run `model-roles init --force` (overwrites files; back up first).
- Keep `model-roles.properties` out of git. It is already in `.gitignore`.
- CLI flags change between versions. If something fails, check `codex exec --help` and `claude --help`.
- Model names (`gpt-6-astra`, `deepseek-flash`) must be ones your account can use. Test them with a quick manual run first.
