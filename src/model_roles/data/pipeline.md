---
description: Plan -> execute -> review using the models set in roles.yaml
argument-hint: <what to build or fix>
---
Run this pipeline for the task: $ARGUMENTS

Use the `roles` MCP tools. Always pass `cwd` as the absolute path of this project.

1. Call `delegate(role="planner", task=...)` with the task and any context you know (key files, constraints).
2. Read the plan. If it is unclear or wrong, call the planner again with your corrections (max 2 rounds). Show me the final plan in short form.
3. Call `delegate(role="executor", task=...)` with the full final plan. Do not write the code yourself.
4. Call `delegate(role="reviewer", task=...)` with the plan and a note of what the executor said it changed.
5. If the verdict is CHANGES_NEEDED, send the review findings back to the executor, then review again (max 3 loops).
6. Finish with: what changed, test results, and any open risks. Check the diff yourself before you say it is done.
