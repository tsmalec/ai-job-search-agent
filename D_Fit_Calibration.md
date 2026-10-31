# D — Fit Calibration (for Step 0)

*SAMPLE DATA for Alex Example. How to grade a posting before any resume work. Edit the scale, screens and years table to match your own rules; `knockouts.py` reads the years table live.*

## Verdict scale
| Verdict | Meaning | Effort |
|---|---|---|
| 🟢 **Strong fit** | Meets every hard requirement, including any years line of the type it asks for, with evidence in `B` for the core responsibilities. | Full loop |
| 🟡 **Real shot** | One material gap (a borderline years line, or one required domain partly covered). Tool gaps are fine here. | Full loop |
| 🟠 **Long shot** | Two or more material gaps, or a required domain the candidate can't honestly reframe. | Light build |
| 🔴 **Skip** | Fails a hard screen (location, pay, required credential) or the core work is a known gap. | None |

## Checks, in order
1. **Hard screens:** from `config.json` (remote / commute radius, base pay floor). `screen_leads.py` runs them.
2. **Knockout questions:** `knockouts.py jd.txt`. An honest "no" on a required line = 🔴.
3. **Required quals count:** X/Y covered by `B`. A missing tool = covered-with-ramp, never a miss.
4. **Domain:** only a *required* domain lowers the verdict.

**Leadership-years math.** The type of years a line asks for sets the count.
| Required line asks for | Counts | Honest years |
|---|---|---|
| Experience building software (total tenure) | Jun 2018 - today | ~8 |
| **Leading or managing programs** (tech lead) | Senior Software Engineer, tech lead since Apr 2024 | ~2 |
| **People leadership / direct reports** | None: tech lead only, no direct reports | ~0 |

## Patterns
- Add patterns here as outcomes come in (what gets screens, what gets rejected fast).

## Summary opener by role type
| Role type | Summary opener |
|---|---|
| Full-stack / frontend / product engineering | SUM-01 |
| Backend / platform / infrastructure | SUM-02 |
