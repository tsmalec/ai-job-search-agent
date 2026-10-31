# E — Style, ATS check, layout, cover letters

*Generic house style. Edit to taste.*

## Writing style
- Plain English. Lead with what the work did, then the number.
- No buzzword openers ("Results-driven", "Seasoned"). No first person on the resume.
- Spell out acronyms once, in both forms: "Quarterly Business Review (QBR)".

## ATS check (simulated; say so)
- `ats_check.py OUT.docx jd.json --jd-text jd.txt`: parse check, required-qual coverage, keyword %, placement, acronyms, skim line.
- Keyword % is a QC check, not a goal. Under 80%: add only what `B` honestly covers, then stop.

## Layout
- Single column, one font (Calibri 10.5), no tables, text boxes, icons or images. `build_resume.py` does this.
- Roles: title line, then "Company, City | Dates". Education on two lines.
- **Visible leadership years:** if a JD sets minimum leadership years, give every role in that window a leadership bullet.

## One-page fit (last step before delivery)
- `onepage.py content.json OUT.docx --jd jd.json`. Pin must-keep bullets; never trim by hand.

## Cover letters, emails, form answers
- Facts from `B` only. Short: why this role, two proof points, one honest gap handled.
- Form answers: `data/form_answers.json` first.

## After delivery
- Log with `log.py`, record with `make_record.py` once the candidate applies.
