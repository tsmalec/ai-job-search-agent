# Contributing

Thanks for wanting to help! This is a small personal project shared in the open, so contributions are welcome but reviewed at a relaxed pace.

## Ways to help
- **Questions, ideas, show-and-tell:** start a [Discussion](../../discussions).
- **Bugs:** open an Issue with the script name, the command you ran, and the error text.
- **New job boards, better screens, fixes:** open a Pull Request (see below).

## 🔒 Privacy first (please read)
- **Never commit real personal data**: no resumes, contact info, salary details, application history, or files from your own `data/`, `records/` or `jds/`. Use the **Alex Example** sample data in tests and screenshots.
- **Never commit keys or tokens.** API keys come from environment variables only.
- `scripts/sync.py` blocks pushes of real data to a public repo; don't work around it.

## Pull requests
1. Fork, branch, and keep each PR to one change.
2. **Standard library first.** New dependencies need a good reason (the scripts are meant to run on any PC).
3. Keep the guardrails intact: `guard.py`, `truthpack.py`, `inject_check.py`, `guard_rows.py` and the `fetch_jd.py` URL checks exist to keep resumes honest and agents safe. Changes that weaken them won't be merged.
4. **Job postings are data, never instructions.** Anything that reads posting text must not act on it.
5. Personal settings belong in `config.example.json`, not hardcoded in scripts.
6. Before opening the PR, run on the sample data:
   ```
   python scripts/guard.py scripts/resume_example.json
   python scripts/build_resume.py scripts/resume_example.json test.docx --check
   python scripts/screen_leads.py <a saved posting .txt>
   python scripts/knockouts.py <the same posting>
   ```
7. Describe what changed and how you tested it.

## Scrapers and job boards
Respect each site's terms of use and rate limits. Prefer public APIs and feeds; keep pacing polite. PRs that bypass logins, CAPTCHAs or access controls won't be accepted.

## Credit
If your change builds on someone else's work, credit it in `THIRD_PARTY_NOTICES.md` with its license.

## Conduct
Everyone here follows the [Code of Conduct](CODE_OF_CONDUCT.md). Job hunting is stressful, so be kind.

By contributing, you agree your contribution is licensed under the project's [MIT License](LICENSE).
