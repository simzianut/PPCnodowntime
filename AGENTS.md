# Agent rules: PPCnodowntime

Several AI agents (Claude Code, Antigravity, others) work on this repo at the same time, for different people on different laptops. **Git is the only coordination channel**: if it isn't pushed, nobody else knows about it. Follow these rules exactly.

## Files that coordinate us
| File | Purpose |
|---|---|
| `TASKS.md` | The task board. Who is doing what, right now. |
| `docs/CONTRACTS.md` | Interfaces between parts: API routes, data schemas, shared types, env vars, ports. |
| `docs/DECISIONS.md` | Append-only log of non-obvious decisions. |

## Every session
1. **Sync first.** `git checkout main && git pull` before anything else. Read `TASKS.md` and `docs/CONTRACTS.md`.
2. **Claim before coding.** Pick a task from **To do**, or add a new one. Move it to **In progress** with your name and branch, then commit and push that change **to `main` immediately**, before writing any code:
   ```
   git add TASKS.md && git commit -m "claim T3" && git push
   ```
   If the push is rejected, someone else changed the board. `git pull --rebase`, re-read `TASKS.md`, and if your task was taken, pick another one. **Never work on a task that isn't claimed by you on `main`.**
3. **Work on your own branch:** `<person>-<agent>/T<id>-short-desc`, e.g. `vlad-claude/T3-orders-api`.
4. **Stay in your lane.** Only edit files that belong to your task. If you need to change something another in-progress task owns, don't. Add a note under that task in `TASKS.md` (push it) or tell your human to talk to its owner.
5. **Contracts first.** If you add or change anything other parts depend on (API route, payload shape, DB schema, shared type, env var, port, folder layout), update `docs/CONTRACTS.md` **on `main` in its own small commit** before or together with the code. Never guess at an interface that isn't written there. Ask for it to be added instead.
6. **Push often.** Small commits, and push your branch at least every ~30 min so others can see progress.
7. **Finish:**
   - `git fetch && git rebase origin/main`, and make sure it builds and runs.
   - Merge to `main` (or open a PR if your human prefers), then push.
   - In `TASKS.md`, move the task to **Done** with a one-line note: what changed and how to test it. Push.
8. **Decisions:** anything a teammate might later ask "why?" about goes in `docs/DECISIONS.md` (date, decision, why, who). Append only.

## Hard rules
- Never `git push --force` to `main`. Never rewrite others' commits.
- Never commit secrets, `.env` files, API keys or tokens.
- Don't reformat or mass-rename files you aren't working on, because it creates conflicts for everyone.
- On a merge conflict in someone else's code, don't resolve it by deleting their changes. Stop and tell your human.
- When unsure, write it down in `TASKS.md` or `docs/DECISIONS.md` and push. Silent assumptions break the team.
