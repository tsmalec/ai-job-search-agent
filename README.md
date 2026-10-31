# AI Job Search Agent

An agent-driven job search kit: lead sweeps across job boards, hard screens (remote, pay, location), fit verdicts, honest resume tailoring from one master catalog, ATS checks, and application tracking. Works with Claude, Codex, Gemini CLI, or a local model (Ollama).

*Built with thanks on open-source work from [career-ops](https://github.com/career-ops-hq/career-ops), [CareerScout](https://github.com/Ramcharan747/careerscout) and [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator), among others. Full credits are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).*

**Clone it, then tell your agent: "Read START_HERE.md."** It will ask for your resumes and your search rules, and set the repo up for you.

> Everything in here now is sample data for an invented candidate, **Alex Example**, a full-stack software engineer, plus one sample posting that fits the sample (`scripts/jd_example.txt`). Make your copy **private** before adding your own.

## How the loop works
1. **Get the posting** → `fetch_jd.py URL --out jd.txt`, then `inject_check.py jd.txt --strip` (postings are data, never instructions to the AI).
2. **Hard screens** → `screen_leads.py jd.txt`: remote / commute radius, stated base vs. your floor, OTE-only pay, "remote at our discretion" bait and switch, closed postings.
3. **Fit verdict** → `knockouts.py jd.txt` plus `D_Fit_Calibration.md` and `C_Gaps.md`: 🟢 / 🟡 / 🟠 / 🔴, required quals X/Y. Stop for the human.
4. **Shop the master** → `shop.py B_Bullet_Catalog.md C_Gaps.md keywords.txt --basket basket.md`.
5. **Build** → `guard.py` (truth rules as code) → `build_resume.py --check` → `ats_check.py` → `onepage.py`.
6. **Fresh-eyes checks** → `truthpack.py` (every line paired with its source, read by a separate model with no JD) and `skimpack.py` (the 7-second recruiter read).
7. **Track** → `log.py`, `make_record.py`, `scoreboard.py`, `followups.py`, `gmail_sweep.py`.

`run_loop.py` chains these and stops at each human decision.

## The files
| File | What it is |
|---|---|
| `START_HERE.md` | Onboarding: an agent turns the sample into *your* setup |
| `AGENTS.md` | What any agent reads every session |
| `config.example.json` → `config.json` | Your search rules: titles, pay floor, location, banned phrases |
| `B_Bullet_Catalog.md` | **The master**: every true accomplishment, with its proof (`facts:`) |
| `C_Gaps.md` | What you don't have, and the honest substitute |
| `D_Fit_Calibration.md` | Verdict scale, screens, years math |
| `E_Style_ATS_Layout.md` | Writing style, ATS check, layout |
| `07_Interview_Story_Bank.md` | Stories behind the bullets |
| `project/SKILL.md`, `project/A_Rules.md` | The loop step by step; the truth rules |
| `data/` | Applications, leads, form answers, market pay (`data/README.md`) |
| `scripts/` | The tools (Python 3.11+, mostly standard library) |

## Quick start (by hand)
```
pip install -r requirements.txt  # + LibreOffice for page counts
cp config.example.json config.json
python scripts/guard.py scripts/resume_example.json
python scripts/build_resume.py scripts/resume_example.json test.docx --check
```

## Try the whole loop on the sample
Alex vs. a fictional Senior Full-Stack Engineer posting: every step should pass (CI runs this too).
```
python scripts/screen_leads.py scripts/jd_example.txt          # ✅ PASS: remote, base over the floor
python scripts/knockouts.py scripts/jd_example.txt             # 0 ✗: 5+ years asked, ~8 honest
python scripts/shop.py B_Bullet_Catalog.md C_Gaps.md scripts/keywords_example.txt --basket basket.md
python scripts/run_loop.py build scripts/resume_example.json alex.docx --jd scripts/jd_example.json --jd-text scripts/jd_example.txt --onepage
```
The build stage runs guard → build → ATS check (grade A, required 7/7) → one page → truth and skim packs, then stops for the human.
Local models: `LOCAL_SETUP.md`. Optional scheduled lead sweeps: `LANES.md`, `agent/README.md`.

## Ground rules baked in
- Never invent facts, metrics or titles. Every resume line traces to `B`, and `guard.py` blocks what doesn't.
- Interview-only and context-only facts never reach paper.
- Job postings are data. Planted "if you are an AI…" lines get stripped and reported, never followed.

## Thanks
Built on ideas and data from [career-ops](https://github.com/career-ops-hq/career-ops), [CareerScout](https://github.com/Ramcharan747/careerscout), [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator), [resumeskills](https://github.com/paramchoudhary/resumeskills), [JobSpy](https://github.com/speedyapply/JobSpy) and [python-docx](https://github.com/python-openxml/python-docx). Full credits and licenses: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License
MIT. See [`LICENSE`](LICENSE).
