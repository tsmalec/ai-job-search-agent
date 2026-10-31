# START HERE — for the agent that just cloned this repo

You're an AI agent (Claude, Codex, Gemini, a local model) and someone just cloned this repo to run their own job search. Everything in it right now is **sample data for "Alex Example,"** an invented candidate. Your first job is to replace Alex with your user. Do this before any resume or triage work.

Work through the steps below **as a conversation**. Ask one step at a time, keep it short, and let the user brain-dump before you organize anything. Never invent facts: if they didn't say it or it isn't in their resume, it doesn't go in.

---

## Step 0 — Privacy check (ask first)
Ask: **"Is your copy of this repo private?"** The files you're about to fill hold their work history, contact info and every job they apply to.
- If it's a public fork: stop and walk them through making a **private** copy (GitHub → "Use this template" → Private, or a new private repo + push). Don't write personal data into a public repo.
- Never commit API keys. Keys go in environment variables (`ANTHROPIC_API_KEY`, etc.).
- `config.json` is git-ignored by default. In a private copy where a scheduled agent on another machine needs it, they can delete that line from `.gitignore`.
- `scripts/sync.py` refuses to push real data to a public GitHub repo.

## Step 1 — "Give me your resumes"
Say: **"Send me every resume you have: current, old, tailored versions, LinkedIn export. More is better: each one may hold a fact the others dropped."**

Then build their files from what they send:
1. **`B_Bullet_Catalog.md`**: their master. Replace Alex's roles block, header, and items with theirs.
   - Roles block: one line per job, `Title | Company, City, ST | Month YYYY - Month YYYY` (the scripts parse this exact shape).
   - One item per accomplishment: `### AISLE-NN · name`, then `role:`, `tags:`, `full:`, `short:`, `facts:`.
   - `facts:` is the proof and the limits (the real number, what they did vs. what the team did). Ask when a number has no source.
   - Anything true but unofficial goes in `interview:`; sensitive or political context goes in `context:`. Neither ever reaches paper.
   - Skills vocabulary: only tools and labels they confirm.
2. **`C_Gaps.md`**: as you go, note anything they say they *don't* have (common asks in their field: certifications, tools). Ask before calling something a gap; gaps often turn out to be real experience under other words.
3. **`D_Fit_Calibration.md`**: fill the leadership-years table from their roles (total tenure / leading programs / people leadership) and the "Summary opener by role type" table with their SUM items.
4. **`07_Interview_Story_Bank.md`**: leave Alex's stories as the format example, or ask for their 3 best stories now.
5. **`data/form_answers.json`**: name, contact, education, work authorization (ask; never guess), voluntary self-ID left `null`.

Check yourself: `python scripts/guard.py scripts/resume_example.json` should now fail (Alex's roles aren't theirs). Rebuild `scripts/resume_example.json` from their B and get it to pass.

## Step 2 — "Tell me your search parameters"
Ask these, one group at a time. Write the answers into **`config.json`** (copy `config.example.json` first; the scripts read `config.json` and fall back to the example).

| Ask | Goes in `config.json` |
|---|---|
| Your name and resume header line (city, phone, email, LinkedIn)? Degree line? | `candidate.*` |
| Remote only, or also hybrid/on-site? If so, home city and how far you'll commute? | `search.home_label`, `commute_radius_mi`, `home_states`, `near_cities` (list the towns in range), `linkedin_location`, `indeed_location`, `usajobs_location` |
| Your time zone (IANA name, e.g. America/Chicago)? | `search.timezone` |
| Minimum guaranteed **base** pay you'd accept? Your target? | `data/pay_rules.json`: `screen_min` (hard screen), `vet_floor` (vetting floor) |
| What job titles are you targeting? Any you never want? | `search.title_patterns`, `lane_words`, `linkedin_terms`, `linkedin_or_groups`, `indeed_queries` |
| Specific employers to watch (Workday boards)? | `search.extra_workday_boards` (`[name, url, note]`) |
| Phrases or claims that must never appear on your resume (titles you didn't hold, customer names)? | `guard.banned_phrases` (`[regex, why]`) |
| Your job titles → short codes for B's `role:` lines; approved alternate title forms | `resume.role_codes`, `resume.title_aliases` |
| Tools you lack that may still go in a skills line? Tools to flag on postings? | `resume.skills_line_ok_gaps`, `resume.flag_tools` |

Also ask their **pay rules** and **dealbreakers** and record them in `D_Fit_Calibration.md` → "Checks, in order", e.g.:
- Does an OTE-only pay range (no guaranteed base stated) count as a fail?
- Does "remote, but in-office at the manager's discretion" count as remote? (The screener fails it by default.)
- Level: what titles are a reach, what's the sweet spot?

## Step 3 — Prove it works
```
python scripts/guard.py scripts/resume_example.json
python scripts/build_resume.py scripts/resume_example.json test.docx --check
python scripts/screen_leads.py scripts/jd_example.txt
python scripts/knockouts.py scripts/jd_example.txt
python scripts/run_loop.py --dry-run screen scripts/jd_example.txt
```
All clean → tell the user they're set up, and that the everyday flow is in `README.md`.

## Step 4 — Clean up
- Delete Alex's sample rows in `data/applications.json` and `data/leads.json` (use `python scripts/log.py`), and the sample emails test if they won't use Gmail.
- Replace `07`'s sample stories as they come up in interview prep.
- Commit and push to **their private** repo.

From then on, follow `AGENTS.md` every session.

**Template marker:** `B_Bullet_Catalog.md`, `C_Gaps.md`, `07_Interview_Story_Bank.md` start with a `TEMPLATE-SAMPLE` line and `data/form_answers.json` has a `_template_sample` key. Delete each one when you replace that file with real data: `sync.py` uses them to refuse pushing real data to a public repo.
