"""model.py — one call, any model. Stdlib only.

    from model import ask; ask("prompt", system="...")
    python scripts/model.py "prompt" [--system "..."] [--system-file f] [--file f] [--model ollama:qwen3:14b]

Backend: --model, else env JOBSEARCH_MODEL, else data/model.json {"model": "..."}, else ollama:qwen3:14b.
Forms: ollama:<name>, openai:<name>, gemini:<name>, anthropic:<name>.
Keys: OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY. Ollama host: OLLAMA_HOST (default http://localhost:11434).
Each call is fresh (no history), which is what the 4b/4c "separate agent" stand-in needs.
"""
import json, os, sys, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = "ollama:qwen3:14b"


class ModelError(RuntimeError):
    pass


def setting(override=None):
    if override:
        return override
    if os.environ.get("JOBSEARCH_MODEL"):
        return os.environ["JOBSEARCH_MODEL"]
    f = ROOT / "data" / "model.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("model") or DEFAULT
        except ValueError:
            pass  # not a parseable value: leave the default
    return DEFAULT


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ModelError(f"{url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, OSError) as e:
        raise ModelError(f"Can't reach {url}: {e}. Is the backend running / online?")


def _key(name):
    k = os.environ.get(name)
    if not k:
        raise ModelError(f"{name} is not set.")
    return k


def ask(prompt, system=None, model=None, timeout=600):
    spec = setting(model)
    backend, _, name = spec.partition(":")
    if not name:
        raise ModelError(f"Bad model setting '{spec}' (use backend:name).")
    if backend == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        r = _post(f"{host}/api/chat", {"model": name, "messages": msgs, "stream": False}, {}, timeout)
        return r["message"]["content"]
    if backend == "openai":
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        r = _post("https://api.openai.com/v1/chat/completions", {"model": name, "messages": msgs},
                  {"Authorization": "Bearer " + _key("OPENAI_API_KEY")}, timeout)
        return r["choices"][0]["message"]["content"]
    if backend == "gemini":
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent?key={_key('GEMINI_API_KEY')}"
        r = _post(url, body, {}, timeout)
        return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    if backend == "anthropic":
        body = {"model": name, "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        r = _post("https://api.anthropic.com/v1/messages", body,
                  {"x-api-key": _key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}, timeout)
        return "".join(b.get("text", "") for b in r["content"])
    raise ModelError(f"Unknown backend '{backend}' (ollama, openai, gemini, anthropic).")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="")
    ap.add_argument("--file", help="append this file's text to the prompt")
    ap.add_argument("--system")
    ap.add_argument("--system-file")
    ap.add_argument("--model")
    ap.add_argument("--which", action="store_true", help="print the model setting and exit")
    a = ap.parse_args()
    if a.which:
        print(setting(a.model)); sys.exit(0)
    p = a.prompt + ("\n\n" + Path(a.file).read_text(encoding="utf-8") if a.file else "")
    sysmsg = Path(a.system_file).read_text(encoding="utf-8") if a.system_file else a.system
    if not p.strip():
        sys.exit("Nothing to send.")
    try:
        print(ask(p, sysmsg, a.model))
    except ModelError as e:
        sys.exit(f"model.py: {e}")
