# Git workflow
- `main` keeps reviewed snapshots; create `dev` from `main` and do daily work there.
- Commit small, logical changes to `dev`; merge reviewed work into `main`.
- Before staging, inspect `git status` and `git diff`; verify local data with `git check-ignore -v <path>`.
- Never stage `.env`, Telegram sessions, databases, logs, CRM/contact exports, phone lists, or credentials.
- If a secret enters history, stop pushing, revoke or rotate that credential, invalidate Telegram sessions, then remove it from Git history before resuming.
