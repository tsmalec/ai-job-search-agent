# Running this repo on your own PC

One-time setup, about 20 minutes. After that, most of the job-search loop runs on the desktop for free and your agent only does the judgment and writing.

## 1. Install (once)
1. **Python 3.11+** from python.org. On the first screen, tick **"Add python.exe to PATH"**.
2. **Git** from git-scm.com (defaults are fine).
3. **LibreOffice** from libreoffice.org (needed for page counts and PDFs).
4. **Ollama** from ollama.com, then in a terminal: `ollama pull qwen3:14b` (or any model you like; `hermes3:8b` works but extracts less accurately).
5. In a terminal (PowerShell):
   ```
   cd $HOME\Documents
   git clone <your private copy's URL> job-search
   cd job-search
   pip install -r requirements.txt
   ```
   Sign in to GitHub if it asks.

## 2. Every session
Open PowerShell in the `job-search` folder and start with:
```
python scripts/sync.py
```
That pulls whatever your agents pushed.

## 3. The everyday commands
| You want to… | Run |
|---|---|
| Screen today's leads | `python scripts/screen_leads.py --leads --fetch` |
| Grab one posting | `python scripts/fetch_jd.py <link> --out work/<name>/jd.txt` |
| Log that you applied | `python scripts/log.py set acme applied` |
| See who to follow up with | `python scripts/followups.py` |
| Scoreboard | `python scripts/scoreboard.py` |
| Save your changes | `python scripts/sync.py "what changed"` |

Building a resume yourself (your agent usually does this):
```
python scripts/guard.py content.json
python scripts/build_resume.py content.json Your_Name_Acme.docx --check
python scripts/ats_check.py Your_Name_Acme.docx jd.json > ats.txt
python scripts/onepage.py content.json Your_Name_Acme.docx --jd jd.json
python scripts/make_record.py content.json --company Acme --role "..." --url ... --ats ats.txt --file Your_Name_Acme.docx --applied 2026-01-05
```

## 4. Rules for local models
- Local models **read** the repo. They never edit `B_Bullet_Catalog.md`, `C_Gaps.md` or `data/applications.json` directly. They **propose** with `scripts/proposals.py`; you approve (`proposals.py approve P001`) and only then it applies.
- Set a bigger context window if you chat with a model about B directly (`/set parameter num_ctx 16384` in `ollama run`). B can grow past 10K tokens.

## 5. Run the full loop with a local or other model
Any model can run the loop now; `AGENTS.md` is its instructions (Codex, Gemini CLI and Claude Code read it through their pointer files).
1. Pick the model once: `setx JOBSEARCH_MODEL ollama:qwen3:14b` (or `openai:gpt-...`, `gemini:...`, `anthropic:...` with that API key set). Check: `python scripts/model.py --which`.
2. Screen a posting: `python scripts/run_loop.py screen work/acme/jd.txt`. It stops at the fit verdict.
3. Build: `python scripts/run_loop.py build work/acme/content.json Your_Name_Acme.docx --jd work/acme/jd.json --jd-text work/acme/jd.txt --onepage`. It stops for the truth/skim reads (fresh `model.py --file review.md`, `--file skim.md`) and for your approval.
4. Edits to B/C/data: `python scripts/proposals.py list`, then approve or reject.

**Swap test before trusting a model:** make 2-3 folders under `work/swap/` from past builds (each: `jd.txt`, `jd.json`, `keywords.txt`, your agent's final `content.json`), then
```
python scripts/swap_test.py --case work/swap/acme --case work/swap/beta --case work/swap/gamma
```
It compares guard pass, truth pre-flags, ATS % and the skim ROLE side by side. Trust the model only if guard passes, it has no more truth flags than your agent, ATS is within 5 points and the skim role matches.

## 6. Gmail: the email sweep and referral emails, no connector

`scripts/gmail_sweep.py` talks to the Gmail API directly (standard library only, nothing to pip install).
Same script for your agent, the sweep agent, or a local model.

**One-time setup (~10 min):**
1. Go to console.cloud.google.com → new project (e.g. "job-search") → APIs & Services → enable **Gmail API**.
2. OAuth consent screen: External, add your own Gmail address as a test user.
3. Credentials → Create credentials → OAuth client ID → **Desktop app** → download the JSON.
4. Save it as `~/.jobsearch/client_secret.json` (outside the repo; `.gitignore` blocks it anyway).
5. `python scripts/gmail_sweep.py auth --client ~/.jobsearch/client_secret.json` → sign in in the browser.
   The token lands in `~/.jobsearch/gmail_token.json`. Scopes: read mail + create drafts/send. Nothing else.

Note: while the consent screen is in "Testing", Google expires the refresh token after 7 days; rerun step 5,
or publish the app (it stays private; Google shows an "unverified app" warning you click through once).

**Every day:**
```
python scripts/gmail_sweep.py sweep            # report only: since last_gmail_sweep
python scripts/gmail_sweep.py sweep --apply    # + confirmations and clear rejections via log.py, then sweep-stamp
python scripts/gmail_sweep.py sweep --fixture scripts/gmail_example.json   # offline test
```
Interviews and offers are printed at the top with a proposed `log.py` line; they are never auto-applied.
After a rejection, add the "likely why": `python scripts/log.py note <row> "Likely why: ..."`.

**Referral emails** (`A_Rules.md`): `draft` saves to Gmail Drafts (safe default). `send` needs `--yes` and is
run only after you approved that exact note. Body is an HTML file with explicit `<a href>` links; one recipient.
```
python scripts/gmail_sweep.py draft --to name@co.com --subject "Referral: <role> (<job #>)" --html note.html --attach OUT.docx
```

## If something breaks
- `fetch_jd.py` can't read a site: open the posting in your browser and paste the text into `work/<name>/jd.txt`.
- `Pages: ?`: LibreOffice isn't installed or isn't in the usual folder.
- Git conflict on `sync.py`: show your agent what it printed.
