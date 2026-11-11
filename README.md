# Model roles for Claude Code

Your main Claude Code session (Claude subscription, Opus or Fable) is the boss.
It hands work to other models by role. `roles.yaml` decides which model does which role.

| Role | Default | Paid by |
|---|---|---|
| planner | GPT-6 Astra via `codex exec` | ChatGPT/Codex subscription |
| executor | DeepSeek V4 Pro via headless Claude Code + DeepSeek API | DeepSeek API key |
| reviewer | Opus via headless Claude Code | Claude subscription |

## Install (global)

Pick one. Each puts a `model-roles` command on your PATH.

```bash
uv tool install /path/to/model-roles      # recommended
pipx install /path/to/model-roles
pip install --user /path/to/model-roles   # plain pip
```

Then:

```bash
npm i -g @openai/codex && codex login     # for the codex backend
model-roles init                          # writes ~/.config/model-roles/{roles.yaml,model-roles.properties}
                                          # and ~/.claude/commands/pipeline.md
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

## Swap models

Edit `roles.yaml`:

```yaml
planner:  { backend: claude,   model: opus }
executor: { backend: codex,    model: gpt-6-astra }
reviewer: { backend: deepseek, model: deepseek-v4-pro }
```

You can also add new roles (e.g. `tester`, `security-reviewer`). They show up automatically.

For the codex backend you can use short names: `astra`, `sol`, `terra`, `luna`. Each one expands to the newest matching model in codex's local model cache (`~/.codex/models_cache.json`), so `sol` becomes `gpt-6.1-sol`. Write the full slug (`gpt-6-sol`) to pin a version. `list_roles` shows what each short name resolves to.

## Notes
- The DeepSeek override applies only to the child process. Your main session stays on the subscription.
- The executor runs with edits auto-accepted (`--sandbox workspace-write` / `acceptEdits`). Use git, and commit before each run.
- Keep `model-roles.properties` out of git. It is already in `.gitignore`.
- CLI flags change between versions. If something fails, check `codex exec --help` and `claude --help`.
- Model names (`gpt-6-astra`, `deepseek-v4-pro`) must be ones your account can use. Test them with a quick manual run first.
