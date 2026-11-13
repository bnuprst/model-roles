---
description: Plan -> review plan -> execute -> test -> review, using the models in roles.yaml
argument-hint: <what to build or fix>
---
Run this pipeline for the task: $ARGUMENTS

Use the `roles` MCP tools. Always pass `cwd` as the absolute path of this project.
Do not write or fix the code yourself.

**Keep your own token use low.** You are only the router. Every role output is saved
on the server with `save_as` and handed to the next role with `attach` - the server
pastes it in full for you. Never re-type a plan, status table, test log or review
into `task`; `task` is only a short instruction. You get back a digest (step ids,
status lines, verdict, last lines); decide from that. Read a saved file under
`.model-roles/` only when the digest is not enough to decide, and only the part you
need. Keep your messages to me short.

A review whose output is only a verdict word (PLAN_ISSUES / CHANGES_NEEDED with no
findings) is a failed call, not a review: call the same role once more with the same
attach and save_as, adding "Your previous answer was only the verdict with no
findings. Write the findings." It does not count as a review round or fix loop.

First call `list_roles(cwd=...)`. Its `pipeline limits:` line gives
PLAN_ROUNDS (`plan_review_rounds`), MAJOR_ROUNDS (`plan_major_rounds`), FIX_LOOPS
(`fix_loops`) and EXEC_RETRIES (`exec_retries`); if the line or a key is missing
use 3, 1, 3 and 1. If `plan_reviewer` is missing, use `reviewer`
for plan review (tell it: review the attached plan, not a diff, and end with PLAN_OK
or PLAN_ISSUES). If `tester` is missing, skip the Test steps and tell `reviewer` to
run the plan's Verification commands itself. Mention any missing role to me once.

## Plan
1. `delegate(role="planner", save_as="plan", task=...)` - the task plus context you
   already know (key files, constraints, how this project is tested). Brief.
2. Round 1: `delegate(role="plan_reviewer", attach=["plan"], save_as="plan_review",
   task="<the original task>. Review the attached plan.")`.
   Round 2+: `delegate(role="plan_reviewer", attach=["plan", "plan_review"],
   save_as="plan_review", task="<the original task>. Re-review: the attached
   plan_review is your previous review; check each finding against the plan's
   Review responses, then report only new BLOCKER/MAJOR issues.")`.
3. If the verdict is PLAN_OK, go to step 4. If it is PLAN_ISSUES, decide from the
   `[BLOCKER]` / `[MAJOR]` lines in the digest (a STILL OPEN finding counts at its
   tag):
   - **Replan** if any BLOCKER is open, or if this was one of the first
     MAJOR_ROUNDS rounds and any MAJOR is open: `delegate(role="planner",
     attach=["plan", "plan_review"], save_as="plan", task="<the original task>.
     Output the full corrected plan for the attached review: fix BLOCKER/MAJOR
     findings with the smallest change that closes them, MINOR only if cheap,
     and answer each open finding in Review responses.")`, then go back to step 2.
   - **Otherwise go on with notes:** only MAJOR/MINOR findings are open after
     MAJOR_ROUNDS rounds. Do not replan; go to step 4 and set NOTES = on. The
     executor and reviewer get `plan_review` attached so the open findings are
     handled during implementation (steps 5, 8, 9).
   **Max PLAN_ROUNDS review rounds.** If a BLOCKER is still open after the last
   round, show me only the open BLOCKER lines from the digest and ask whether
   to continue with notes, revise once more, or stop.
4. Show me the final plan's step ids, titles and Definition of done in short form,
   taken from the digest, plus the open MAJOR lines if NOTES is on.

## Execute
5. `delegate(role="executor", attach=["plan"], save_as="exec",
   task="Implement every step of the attached plan. Report status per step id.")`
   If NOTES is on: `attach=["plan", "plan_review"]` and add to the task "The
   attached plan_review lists open findings against the plan: handle each one
   in the step it names, with the smallest change that closes it, and report
   it as FIXED or NOT FIXED after the status table." 
6. Check the status lines in the digest. If any step is PARTIAL, SKIPPED or missing,
   call `executor` once more with `attach=["plan", "exec"]`, `save_as="exec"` and
   task "Finish only steps <ids>; the attached exec report says why they were not
   done." Do this at most EXEC_RETRIES times; then carry the remaining gaps forward as
   known-incomplete and tell me.

## Test
7. `delegate(role="tester", attach=["plan"], save_as="tests",
   task="Run the attached plan's Verification commands section, nothing else.")`
   The server checks the working tree around the call (`read_only_check`); if the
   result carries a WARNING that the tree changed, tell me and the reviewer, and
   treat the test results as untrusted.
   If `executor` or `tester` reports BLOCKED, "not permitted", "sandbox" or
   "cannot run" for a project command (`./gradlew`, `npm`, `pytest`, ...), that is a
   config problem, not a code problem: tell me to add `allowed_tools: [Bash]`
   (claude/deepseek) or `network: true`/`writable_dirs:` (codex) for that role in
   `roles.yaml`, and stop that stage. Do **not** run the build or test commands
   yourself instead.

## Review
8. First review: `delegate(role="reviewer", attach=["plan", "exec", "tests"],
   save_as="review", task="Review the current diff against the attached plan,
   executor report and test results.")` (add a one-line note if the tester
   WARNING fired, and name any known-incomplete steps from step 6). If NOTES is
   on, also attach `plan_review` and add "The attached plan_review findings were
   handed to the executor as notes: changes that address them are planned, and
   any still not handled is a finding at its original tag." 
   After a fix loop: `attach=["plan", "exec", "tests", "review"]` (plus
   `plan_review` if NOTES is on), same
   `save_as`, task "Re-review: the attached review is your previous one. Mark
   each finding RESOLVED or STILL OPEN, then report only new BLOCKER/MAJOR
   issues in code changed since."
9. If the verdict is CHANGES_NEEDED: `delegate(role="executor", attach=["plan",
   "review"], save_as="exec", task="Fix the BLOCKER and MAJOR findings in the
   attached review; MINOR ones only if trivial.")`, then repeat steps 7 and 8.
   **Max FIX_LOOPS fix loops.** If CHANGES_NEEDED remains after the last one,
   stop and list the open BLOCKER/MAJOR lines in the Finish report.

## Finish
10. Report briefly: files changed (`git diff --stat`, not the full diff), the
    tester's final result, each Definition-of-done item met or not (from the review
    digest), open risks, and how many plan rounds / fix loops were used and
    whether the plan went ahead with notes. List
    MINOR findings that were left unfixed in one line each, at most 5.
