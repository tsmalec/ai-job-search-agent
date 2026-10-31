# Security Policy

## Reporting a vulnerability
Please **don't open a public issue** for security problems. Report privately through GitHub:
**Security → Report a vulnerability** on this repo ([direct link](../../security/advisories/new)).

Include the script, what an attacker could do, and steps to reproduce (use the Alex Example sample data, never real personal data). You'll get a reply as soon as practical; this is a small personal project.

## In scope
- Personal data leaking into commits or pushes (`sync.py` public-repo guard, `.gitignore`)
- Unsafe fetches (`fetch_jd.py` URL and redirect checks)
- Prompt injection from job postings getting past `inject_check.py`
- Token or key handling (`gmail_sweep.py`, `model.py`)
- Row-loss or overwrite bugs (`sync.py` row guard, `guard_rows.py`)

## Supported versions
Only the latest `main` is supported.

## Trust boundaries to know about
- **Behind an HTTP(S) proxy,** `fetch_jd.py` still refuses non-public IP literals, local names and hostnames that resolve to non-public addresses, but the proxy makes the final connection, so the connect-time address check can't run. Only use a proxy you trust not to reach internal services.
- **Hosted models** (`scripts/model.py` with Anthropic, OpenAI or Gemini) receive whatever you send them: your resume facts, postings and prompts. A local model (Ollama) keeps that on your machine, as long as the endpoint is one you control.
- **Gmail access** uses read + compose scopes. The scripts only send with `--yes`, one recipient at a time, and only auto-update rows from mail that passes DKIM from the company's or a known hiring system's domain, but the granted scope itself can send.
- **`inject_check.py` is a signal, not a wall.** Postings are always treated as data, never instructions; the scanner flags planted text for you and the agent to review.
