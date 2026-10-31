# data/

Job-search tracking. Sample rows are for Alex Example; replace them (START_HERE.md).

| File | What it is | Written by |
|---|---|---|
| `applications.json` | One record per posting triaged or applied to | The agent via `log.py`; email sweep (status only) |
| `leads.json` | Leads not yet worked | Sweep agent; interactive agent when triaging |
| `watchlist.json` | Local employers opening or expanding | You or a news-watch agent |
| `archive.json` | Closed rows (skipped, passed, dropped, closed), same schema. Moved by `python3 scripts/log.py archive` (run it weekly or when the live file feels big) | `log.py` |
| `seen.txt` | Generated dedupe list (company \| role \| url \| status) for every row in both files. Rebuilt on every `log.py` save | `log.py` |

Scoreboard: `python3 scripts/scoreboard.py` (counts live + archive). Counts are computed, never stored.
Always change rows with `scripts/log.py`, never by hand, so `seen.txt` stays current.
Excel: Data > Get Data > From File > From JSON, then expand the `applications` list.

## applications.json record
| Field | Values |
|---|---|
| `added` | Date first logged (`YYYY-MM-DD`) |
| `company`, `role` | Role keeps posting IDs, location and pay in parentheses |
| `verdict` | `green`, `yellow`, `orange`, `red`, `green/yellow`, or `null` (applied outside the loop) |
| `ats` | Score text as given (e.g. `"92"`, `"88% (A, req 5/5)"`) or `null` |
| `resume` | Resume record or file sent, or `null` |
| `status` | `applied`, `rejected`, `interview`, `offer`, `triaged`, `deciding`, `fit_check`, `resume_built`, `skipped`, `passed`, `dropped`, `closed` (must match `STATUSES` in `scripts/log.py`) |
| `status_date` | Date of the current status |
| `confirmed` | `true` confirmation email seen · `false` searched, none found · `null` unknown |
| `referral` | Who referred and when, or `null` |
| `note` | Detail behind the status |
| `live` | (optional) Is the posting still up: `open` \| `closed` \| `uncertain`. Separate from `status`; never changes it. Set by `log.py live <match> <state>` or `--live` on add/promote/set |
| `live_checked` | Date `live` was last set (auto) |
| `next_step` | Contacts, gaps, follow-ups; `Closed` when done |
| `url` | Posting link (records added on; older records keep IDs in `role`) |
| `years_req` | (optional) The JD's required years line: `{"type": "total" \| "program" \| "people" \| "domain", "asks": "<short wording>", "status": "met" \| "borderline" \| "short"}`. Type and counts per `D_Fit_Calibration.md` leadership-years math. Fill at Step 0 for every new triage; omit when the JD has no years line. |
| `screen` | (optional) First sign of progression past submission: assessment, questionnaire, or recruiter contact, with date. Set with `log.py set <match> applied --screen "questionnaire 2026-01-08"`. Status stays `applied` until a recruiter screen books (`interview`). Read by `scoreboard.py --fit`. |
| `timing` | (optional) Response-speed tracking, local time: `{"applied_at": "2026-01-05T09:00", "outcome_at": "2026-01-08T15:30", "outcome_type": "rejected" \| "interview" \| "offer", "system": "Jobvite", "source": "..."}`. Date-only stamps are allowed when the time is unknown. `outcome_*` is the **first** employer response and is not overwritten. Filled by `log.py set ... applied` / `rejected` (pass `--at` with the email time and `--system`); view with `scoreboard.py --timing`. Log only until ~20 outcomes; no rule changes from it before then. |

**Core vs. stretch:** `python3 scripts/scoreboard.py --fit` shows screen, rejection and pending rates by `years_req.status` and by verdict, with pending rows split by age (14+ days). Don't draw conclusions until most rows are 14+ days old.

**Calibration check:** to test whether verdicts predict results, compare the share of `interview`/`offer` vs. `rejected` vs. no response (`applied`) by `verdict` and by `years_req.status`, using days since `status_date` for applied rows. A recruiter screen is logged as `interview` with "Recruiter screen" in `note`, as soon as an email books one.

**Quiet applications (owner: the agent):** every Monday's first triage chat runs `python3 scripts/followups.py`, shows the candidate the list, and the candidate picks who gets a nudge. The agent drafts the nudges; nothing is sent without the candidate's OK. Log a nudge in that row's `note` with `log.py set <match> applied --note "Nudged <who>"`.

Interview: `status: "interview"`, `status_date`, and who asked for what in `note`.

## leads.json record
See `agent/README.md` ("One schema, one write protocol"). `leads.json` holds only the sweep agent's `not_triaged` leads (plus `dropped_by_agent`). The agent moves every lead out with `log.py promote --status <triaged|skipped|passed> --note "<why>"`; anything the agent has rated or set aside lives in `applications.json` as `triaged`, with the reason in `note`. The old `rated` / `not_checked` / `tier1_next` lead statuses were retired (26 rows moved to `applications.json` as `triaged`).

## Standing rules (defaults; edit to taste)
- **Status changes:** only when an email clearly says so. Never mark "likely." Open the email body first.
- **Location and pay:** set in `config.json`; `screen_leads.py` enforces them.
- **Rejections:** add a one-line "likely why" to `note` (location, required quals, wrong level) and use past notes in future verdicts.

## Helper tools (read-only unless noted)
- **`jd` field (row):** path of the archived posting, e.g. `jds/Acme_Program_Manager_12345678.txt`. Set by `make_record.py --jd` (or `--jd-only` to backfill). `closing_soon.py` and `negotiate.py` read it.
- **`data/pay_facts.json`** (`salary_gap.py add`, append-only): `{"facts": [{"key": row url or "company | role", "company", "role", "kind": posted|recruiter|offer|asked, "low", "high", "source", "date", "note"?}]}`. Never edit or delete a fact; add a newer one. No target numbers.
- **`data/busy.json`** (optional, your recurring commitments for `invite_reader.py`): `{"weekly": [{"day": "Tue", "start": "17:00", "end": "19:00", "what": "..."}]}`. Missing = the script says to check them by hand.
- Scripts: `reposts.py` (flag, never skip; `reposts.check()` for triage), `closing_soon.py`, `salary_gap.py`, `negotiate.py` (offer stage, B numbers only), `skip_blockers.py`, `invite_reader.py` (proposes a `log.py set ... interview` line; sends nothing), `role_clarity.py` (`clarity(text)["flag"]`: 3+ of 6 facts missing = 🟡, never a skip), `calibrate.py` + `funnel_speed.py` (advisory until ~20 outcomes), `dashboard.py out.html`.
