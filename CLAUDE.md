# Claude Code Instructions

## Git Commits
NEVER commit unless explicitly asked by the user.

## Git Checkout
When asked to load a previous git commit, use `git checkout` to load that commit exactly. Do not try to edit files, make modifications, or do workarounds. Load it and stop.

## Git Commit Naming
When you are told to "call it X" or "name it Y", use that exact name verbatim in the commit message. Do not rewrite, improve, or modify the name. If the user says "call it polygon tools can flip to normals", the commit message is literally "polygon tools can flip to normals".

## DRAW_BUGS.md — Debugging Knowledge Base
Whenever you fix a bug, add an entry to `DRAW_BUGS.md` documenting:
1. **What was wrong** — the symptom/behavior
2. **Why it happened** — root cause analysis
3. **Why this was hard to debug** — the meta-level trap (what made it confusing or non-obvious)
4. **What was fixed** — the solution and changed code
5. **Rule of thumb** — actionable guidance to avoid this in the future

The goal: when this bug appears again (or a similar one), you'll recognize it immediately and know exactly how to fix it. DRAW_BUGS.md is your future self's debugging playbook.

## Tone
All responses should be casual, conversational, and fun to read.
