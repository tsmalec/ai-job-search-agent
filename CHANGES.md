# Changes

What changed in each publish, newest first.

## Why the git history is one commit

This repo is a cleaned-up public copy of a private job-search toolkit. Every publish **replaces the whole
git history with one fresh commit**. That's on purpose:

- **Privacy.** Early publishes let some personal details slip into the public copy (sample data and notes
  copied from the private toolkit, and private AI-session links in commit messages). Deleting them in a new
  commit wouldn't have helped: anyone can still open the older commits, and GitHub keeps commits behind old
  pull requests (for example, automated dependency PRs) indefinitely. Replacing the history is the only way
  to make a removed detail actually gone.
- **Every publish is screened first.** Before anything is pushed, the whole repo is checked against a private
  list of personal terms, scanned for real contact details and real data files, and the tests are run. If
  anything fails, nothing is pushed.
- **What you'll see:** one commit on `main`, its title describing the latest publish, and no other
  branches. The commit date is the publish day in UTC (noon), not a real time of day.
- **What you won't see:** line-by-line history, blame, or older versions of files. This file is the
  history instead. Each entry is written by the publish script, from the description given at publish time
  and the list of files that changed.
- **Starts 2026-10-31.** Publishes before that aren't listed: their history was removed for the reasons above.
  Credits and licenses for outside work are in `THIRD_PARTY_NOTICES.md`.

## Publishes

### 2026-10-31 · Gmail sweep and tests generic; adjustments sign-off rule; public-repo rule stays private

- changed: `.gitignore`
- changed: `LANES.md`
- changed: `scripts/gmail_sweep.py`
- changed: `scripts/log.py`
- added: `tests/test_gmail_classify.py`
- added: `tests/test_gmail_guard.py`
- added: `tests/test_gmail_match.py`
- added: `tests/test_gmail_raw.py`

### 2026-10-31 · Pay thresholds read from data/pay_rules.json (sample values in public); off-list, screen and vetting floors follow it

- changed: `START_HERE.md`
- changed: `config.example.json`
- changed: `data/title_pay.json`
- changed: `scripts/board_sweep.py`
- changed: `scripts/common.py`
- changed: `scripts/salary_gap.py`
- changed: `scripts/screen_leads.py`
- changed: `scripts/source_sweep.py`
- changed: `tests/test_public_guard.py`
- added: `data/pay_rules.json`

### 2026-10-31 · Changes file: explain why the git history is one commit

- only this file (`CHANGES.md`)

### 2026-10-31 · Changes file: list the files behind the first entry

- only this file (`CHANGES.md`)

### 2026-10-31 · Credits: per-script sources, what we changed in each borrowed idea, upstream commits and versions, resumeskills notice; adds this CHANGES file

- changed: `THIRD_PARTY_NOTICES.md` (per-script sources, what we changed, upstream commits and versions, resumeskills notice, how the copy check works and its limits)
- changed: `README.md` (adds resumeskills to the credits line)
- added: `CHANGES.md`
- Note: the two credits files went out in an earlier publish the same day under the old fixed commit label; this entry describes them.
