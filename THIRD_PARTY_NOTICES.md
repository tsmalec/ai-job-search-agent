# Third-party notices and thanks

This project stands on other people's generous open-source work. Thank you to every author below.

## Included in this repository

### Job-board company list: Feashliaa/job-board-aggregator
`data/sweep_boards.json` ("names") contains company board slugs and names from
[Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) (`data/*_companies.json`),
and `scripts/board_sweep.py --refresh-boards` downloads updates from it. This is data, not code: our scripts
only read it. The included copy matches this commit (the newest on `main` when we first took it:
[`4f50652`](https://github.com/Feashliaa/job-board-aggregator/commit/4f506522b6b0ab69b845e733ab3758c3ffc8459d));
`--refresh-boards` pulls whatever is newest on `main`. Used under the MIT License:

```
MIT License

Copyright (c) 2026 Riley Dorrington

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Weak-phrase list in `ats_check.py`: paramchoudhary/resumeskills
`scripts/ats_check.py` flags vague bullet wording with a `VAGUE` list of 14 short phrases. Our build notes say
this list came from the open-source **resume-bullet-writer** skill in
[paramchoudhary/resumeskills](https://github.com/paramchoudhary/resumeskills)
(`skills/resume-bullet-writer/SKILL.md`; commit current when we read it, and still its head:
[`74ae19e`](https://github.com/paramchoudhary/resumeskills/commit/74ae19e7c62b0516d1c298328e5544976c12da5d)).
Checked against that file: 6 of our 14 phrases appear in its "weak bullet" examples
("worked with stakeholders", "improved processes", "responsible for", "helped with", "assisted with", "tasked
with"); the other 8 we added ("worked closely with", "duties included", "involved in", "a variety of", "worked
on", "in charge of", "handled various", "various"). No sentences or other text were taken. Short stock phrases
like these probably aren't protected on their own, but because our list was drawn from their file, we keep their
notice here to be safe:

```
MIT License

Copyright (c) 2026 Resume Skills

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Ideas and methods (our own code, credited with thanks)

These projects gave us ideas and methods. Our scripts are independent Python (stdlib) rewrites: the originals are
JavaScript (career-ops) and Go (CareerScout), and no source code was copied or translated line by line.

**How this was checked, and what the check can't prove:**
- Each script below was compared with its closest original module: every line of 30+ characters and every
  string literal of 10+ characters. Result: no shared lines. The only shared strings are public facts such as
  platform host names (`.greenhouse.io`, `.myworkdayjobs.com`, `.recruitee.com`, `.breezy.hr`), API field
  names (`department`, `location`), HTTP header names, the public S&P 500 list's URL, and plain phrases
  (`work from home`, `has been filled`, `united kingdom`).
- A text comparison catches copying, not a close re-telling of someone's logic in another language. It can't
  rule that out on its own.
- The code was written with an AI assistant (Claude). We didn't keep a record of exactly what the assistant
  read from each original project, so it may have seen their descriptions or code, and its design could echo
  theirs more closely than the comparison shows. That's why every script that took an idea is listed here, with
  what we changed.
- The originals were matched to our scripts by name and purpose after the fact. Our original build notes credited
  each *project*, not each *file*.

Versions: we didn't record a commit when we read these projects, so the commit below is the newest one on each
project's default branch at the end of the day we read it (an approximation, not a recorded fact).

### [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops)
MIT, Copyright (c) 2026 Santiago Fernández de Valderrama. Commit (approximate, see above):
[`a947686`](https://github.com/career-ops-hq/career-ops/commit/a94768663b94b1f1dd314b45e7a49c356bd393b7).

| Our script | Their module (closest match) | Idea we took | What we changed |
|---|---|---|---|
| `board_sweep.py` | `scan-ats-full.mjs` | Walk every company on the big ATS platforms, not just a tracked list | Python stdlib instead of Node; company lists from the Feashliaa dataset instead of their directories; each platform runs in its own parallel lane with spare workers handed to slower lanes; pacing and timeouts tune themselves per platform; dead boards are remembered and rechecked monthly; a crashed run resumes from a checkpoint; every hit gets a full posting read and our remote/pay screen; an "off-list" catch keeps unlisted titles with high stated pay |
| `check_live.py` | `liveness-core.mjs` | Check a posting is still open; normalize curly quotes and accents before matching closed banners | Plain text rules, no browser (theirs has a browser mode); our own banner list; three answers (OPEN / CLOSED / UNCERTAIN) where UNCERTAIN only flags and never skips; catches pages that redirect to a job list; a Greenhouse "not on the board" answer triggers a title lookup instead of a "closed" call |
| `reposts.py` | `detect-reposts.mjs` | Same company + role under a new URL, on 2+ dates within 90 days = repost | Reads our JSON logs and dedupe list instead of a scan-history TSV; strips req numbers and parentheticals from titles; `--leads` mode checks the untriaged inbox; flag only, never a skip |
| `role_clarity.py` | `role-clarity.mjs` | Check a posting for six facts a candidate needs | Different six facts: theirs are supervisor, scope, deliverables, success measures, coordination, cadence; ours are boss/department, scope, team, pay, location, start date. Missing 3+ gives a yellow "vague posting" flag; it never changes a verdict |
| `salary_gap.py` | `salary-gap.mjs` | Append-only pay facts, compared with the posted range | Three kinds (posted / recruiter / offer) instead of their desired/advertised/actual tiers; adds a market check from our title pay table; deliberately drops their "desired" target: it never suggests a number |
| `negotiate.py` | `negotiation-roi.mjs` | Safety gate: a number goes in a talking point only if it's confirmed in a second source | Our gate checks the master bullet catalog (B) instead of a story bank against cv.md; uses B's approved wording, ranked by overlap with the posting; no dollar-ROI estimate; never reads interview-only or context-only facts; runs only at offer stage |
| `closing_soon.py` | `closing-soon.mjs` | Read closing dates from notes and the archived posting; group overdue and unparsed so nothing drops | Our date phrasings and open-status list; reads our JSON log and `jds/` files |
| `calibrate.py` | `calibrate.mjs` | Do higher fit grades convert better? Advisory only | Our four-color verdicts; counts silence as "no response" only after 14 days; leaves rows under 14 days out of the rates |
| `funnel_speed.py` | `funnel-velocity.mjs` | Days per pipeline stage | Built from our row timestamps instead of a status ledger; no market benchmarks; adds a silence curve (share of rejections by day 1/3/7/14/30) |
| `skip_blockers.py` | `skip-blockers.mjs` | Rank the requirements behind roles you skip | Reads free-text notes and years lines instead of structured evaluator output; splits learnable blockers (years, certs, domains) from screen blockers (pay, location, closed) |
| `invite_reader.py` | `ics-interview.mjs`, `invite-match.mjs`, `panelist-roles.mjs` | Read an .ics or invite email; match it to an application; sort the panel by role | One script instead of three; adds a check against the candidate's weekly busy times; prints a proposed log line and changes nothing until the candidate OKs it |
| `make_record.py --jd`, `attach_jds.py` | `archive-posting.mjs` | Keep the posting after it comes down, for interview prep | Saves plain text instead of a browser-rendered PDF; every saved text first goes through our planted-AI-instruction check; linked from the application row and the record |
| `dashboard.py` | `dashboard/` (Go terminal app) | A pipeline dashboard | One self-contained HTML page (light and dark themes) built from our scoreboard, follow-ups and closing-soon data, instead of a terminal app |

### [Ramcharan747/careerscout](https://github.com/Ramcharan747/careerscout)
MIT, Copyright (c) 2026 Ramcharan. Commit (also its current head):
[`eee84fa`](https://github.com/Ramcharan747/careerscout/commit/eee84faf02f8e46dc3c5764c09d1880816577539).

| Our script | Their module (closest match) | Method we took | What we changed |
|---|---|---|---|
| `board_sweep.py` (SmartRecruiters, Recruitee, Rippling, Breezy, Workable) | `internal/atsprober/probers.go` | Which platforms have public job feeds, and where they live | Our own feed readers and filters, run inside our sweep lanes; Breezy read from its public `/json` feed because their API address needs a token; Workable kept off after it rate-limited our tests |
| `ats_probe.py` | `cmd/probe_ats/main.go`, `cmd/verify_companies/main.go` | Guess a company's board name and ask the board to confirm; seed with the S&P 500 list | Python instead of Go, no database; also seeds from the companies we've already logged and our watchlist; confirmed boards go to `sweep_boards.json` with a note saying where each came from |
| `wd_discover.py` | `cmd/harvest_urls/main.go`, `cmd/probe_workday/main.go` | Find Workday sites in archived-URL indexes, then confirm each is live | Wayback Machine CDX index (Common Crawl as a second index), paged and de-duplicated; pulls tenant, environment and site from each link; one job-API call per new board; saves only live boards with open postings |

### [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy)
`linkedin_sweep.py` reads the same public, no-login LinkedIn pages JobSpy reads. Idea only; our own code, built
around our own rate-limit findings. (JobSpy itself is listed under libraries below.)

### Drafter-reviewer check (`truthpack.py`): source not confirmed
Our build notes say the fresh-eyes truth check (a second AI that reads only the finished resume
lines and their sources, and passes or flags each one) came from "hirebot's drafter-reviewer pipeline." We
couldn't confirm which project that was. The closest public match we found is
[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search), which uses a drafter and a reviewer
agent. Idea only; the script is our own. If this is your idea, please open an issue and we'll credit you properly.

### Research that shaped the rules (no code)
- **A widely cited 2018 recruiter eye-tracking study** (publisher name withheld by our privacy screen): recruiters' first screen lasts ~7 seconds and lands on the current
  title, employer, dates and first bullets. Basis for the first-two-bullets rule and `skimpack.py`.
- **Hirecarta** (ATS vendor breakdown), **Enhancv** (recruiter survey) and **scale.jobs:** basis for the
  knockout-question check (`knockouts.py`) and the placement, acronym and date-format checks in `ats_check.py`.

## Libraries you install (not bundled)

- **[python-jobspy](https://github.com/speedyapply/JobSpy)** (MIT, Copyright (c) 2023 Cullen Watson), pinned `1.4.1` in `requirements.txt`: Indeed search in `source_sweep.py`.
- **[python-docx](https://github.com/python-openxml/python-docx)** (MIT, Copyright (c) 2013 Steve Canny), pinned `1.2.0`: builds the .docx resumes.
- **[LibreOffice](https://www.libreoffice.org/)** (MPL 2.0): page counts and PDF conversion.

## Data and services used at runtime

- **[datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies)** (Open Data Commons PDDL 1.0, public domain): company list for `ats_probe.py`.
- **[Internet Archive Wayback Machine](https://web.archive.org/)** and **[Common Crawl](https://commoncrawl.org/)**: URL indexes for `wd_discover.py`.
- **[USAJOBS API](https://developer.usajobs.gov/)**: federal postings in `source_sweep.py`.
- Public job-board APIs from Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Recruitee, Rippling and Breezy.
  Please respect each site's terms of use and rate limits; LinkedIn's terms restrict automated access, so use `linkedin_sweep.py` at your own discretion.

## Models

Built and run with AI assistance from **Claude** (Anthropic). `model.py` also supports **Ollama** (e.g. Qwen), **OpenAI** and **Gemini** models; each is subject to its provider's terms.
