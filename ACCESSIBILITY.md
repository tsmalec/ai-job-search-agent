# Accessibility

This project should be usable by everyone running a job search, including people who use screen readers, keyboard-only navigation, magnification, or who process information differently (ADHD, dyslexia, anxiety). Job hunting is hard enough.

## What it's built around
- **Text first.** Everything runs from a terminal or through an AI agent in plain conversation; no mouse-only steps.
- **Short, scannable output.** Scripts print one finding per line with a clear mark (✓ / ⚠ / ✗ / FAIL / OK) *and* a word, so meaning never depends on the symbol or its color alone.
- **Agents keep replies short.** `project/A_Rules.md` asks agents for brief, scannable replies and finished files instead of drafts to review.
- **Accessible resumes.** `build_resume.py` makes single-column .docx files with real text, no tables, text boxes, icons or images, and one font, which also suits screen readers and text-to-speech.
- **Dashboard:** `dashboard.py` writes plain HTML tables with text labels and standard links.

## Supported environments
Windows, macOS and Linux with Python 3.11+, any terminal, and any agent that can run commands (Claude, Codex, Gemini CLI, Ollama models).

## Known limitations
- Emoji in script output (🛡️, 📌, ⛔) may be read aloud by screen readers; each is paired with words.
- The HTML dashboard hasn't been formally tested against WCAG 2.2 or with every screen reader.
- Some scripts print wide tables that can wrap in narrow terminals or under magnification.
- Page counts need LibreOffice, and Gmail setup goes through Google's web console, so their accessibility depends on those tools.

## Report a barrier
Open an Issue (or a Discussion if you're not sure it's a bug) describing what you were trying to do, your setup (OS, assistive tech, agent), and what got in the way. Accessibility reports are treated as bugs, not feature requests.
