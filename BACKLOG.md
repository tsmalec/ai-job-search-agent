# Backlog — the to-do tracker

Every approved change to your setup lives here until it's done. `python scripts/backlog.py` lists open items, overdue first; every `sync.py` push prints what's overdue.

- Add: `python scripts/backlog.py add "<item>" --due YYYY-MM-DD --group <group>`
- Finish: `python scripts/backlog.py done B01 "what shipped"` (never hand-edit the status). Recurring chores say `(every 7d)` and re-open with the next due date.
- Status: `open` · `needs-user` · `done YYYY-MM-DD` · `dropped YYYY-MM-DD`.

| ID | Item | Group | Due | Status |
|---|---|---|---|---|
| B01 | Set up from START_HERE.md: replace Alex Example with your resumes and search rules | setup | 2026-11-01 | open |
| B02 | Chore (every 7d): quiet applications: `followups.py`, pick who gets a nudge | chore | 2026-11-05 | open |
| B03 | Chore (every 7d): `scoreboard.py` + `log.py archive` | chore | 2026-11-05 | open |
