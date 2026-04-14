"""
Upload historical chat/incident data into Azure AI Search.

Run standalone (from the Deploy/ root directory):
    python tools/upload_history.py

Opens a web page at http://localhost:5001 where you can:
  - paste plain text (one incident per block, separated by blank lines)
  - paste JSON (single object or array of objects)
  - upload a .json or .txt file

Each entry is saved to the database with a generated ID and an embedding.
"""
import os, sys, json, re, time

# Allow running from tools/ subdirectory — add the Deploy root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, render_template_string

# Fix encoding for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database import save_or_update_incident

app = Flask(__name__)

# ─── HTML page ───────────────────────────────────────────
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Upload History to AI Search</title>
<style>
  :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #e6edf3;
          --accent: #58a6ff; --green: #3fb950; --red: #f85149; --muted: #8b949e; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 2rem; }
  h1 { color: var(--accent); margin-bottom: .5rem; font-size: 1.5rem; }
  .sub { color: var(--muted); margin-bottom: 1.5rem; font-size: .9rem; }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
  label { display: block; font-weight: 600; margin-bottom: .4rem; color: var(--accent); }
  textarea { width: 100%; min-height: 220px; background: var(--bg); color: var(--text);
             border: 1px solid var(--border); border-radius: 6px; padding: .8rem;
             font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: .85rem;
             resize: vertical; }
  input[type=text] { width: 100%; padding: .5rem .8rem; background: var(--bg); color: var(--text);
                     border: 1px solid var(--border); border-radius: 6px; margin-bottom: .8rem; }
  input[type=file] { margin: .8rem 0; color: var(--muted); }
  .row { display: flex; gap: 1rem; margin-bottom: .8rem; }
  .row > div { flex: 1; }
  button { background: var(--accent); color: #000; border: none; padding: .6rem 1.6rem;
           border-radius: 6px; font-weight: 600; cursor: pointer; font-size: .95rem; }
  button:hover { opacity: .85; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  .or { text-align: center; color: var(--muted); margin: 1rem 0; font-weight: 600; }
  #result { margin-top: 1rem; padding: 1rem; border-radius: 6px; display: none;
            font-family: monospace; font-size: .85rem; white-space: pre-wrap; }
  #result.ok { display: block; background: #0d1f0d; border: 1px solid var(--green); color: var(--green); }
  #result.err { display: block; background: #1f0d0d; border: 1px solid var(--red); color: var(--red); }
  .help { color: var(--muted); font-size: .82rem; margin-top: .4rem; }
</style>
</head>
<body>

<h1>📥 Upload History to AI Search</h1>
<p class="sub">Paste or upload old channel history to append it into the incident database.</p>

<div class="card">
  <label>Default Subject (optional)</label>
  <input type="text" id="subject" placeholder="e.g. Migrated from old channel">

  <label>Paste text or JSON</label>
  <textarea id="content" placeholder='Paste plain text, or JSON like:
{
  "subject": "pod crash",
  "log": "CrashLoopBackOff in namespace prod",
  "diagnosis": "OOM — increase memory limit",
  "chat_history": "Q: why? A: container hit 256Mi limit"
}

Or an array:  [ { ... }, { ... } ]

For plain text, separate multiple entries with a blank line.'></textarea>
  <p class="help">Plain text → each block becomes the "log" field. JSON → fields mapped directly.</p>

  <div class="or">— OR —</div>

  <label>Upload a file (.json or .txt)</label>
  <input type="file" id="file" accept=".json,.txt,.log,.csv">

  <br>
  <button id="btn" onclick="upload()">Upload to Database</button>
  <div id="result"></div>
</div>

<script>
async function upload() {
  const btn = document.getElementById('btn');
  const res = document.getElementById('result');
  const subject = document.getElementById('subject').value.trim();
  const content = document.getElementById('content').value.trim();
  const file = document.getElementById('file').files[0];

  btn.disabled = true;
  res.className = ''; res.style.display = 'none'; res.textContent = '';

  let body = {};
  if (file) {
    const text = await file.text();
    body = { raw: text, subject: subject, filename: file.name };
  } else if (content) {
    body = { raw: content, subject: subject };
  } else {
    res.className = 'err'; res.textContent = 'Paste content or pick a file first.';
    btn.disabled = false; return;
  }

  try {
    const resp = await fetch('/api/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (resp.ok) {
      res.className = 'ok';
      res.textContent = `✅ ${data.saved} entry/entries saved.\n\nIDs: ${data.ids.join(', ')}`;
    } else {
      res.className = 'err';
      res.textContent = `❌ ${data.error}`;
    }
  } catch (e) {
    res.className = 'err';
    res.textContent = `❌ Network error: ${e}`;
  }
  btn.disabled = false;
}
</script>
</body>
</html>
"""


def _generate_id():
    """Timestamp-based ID (same pattern the bot uses)."""
    return str(int(time.time() * 1000))


def _try_parse_json(raw):
    """
    Try to parse as JSON.  If strict parsing fails, normalise common
    issues (unquoted keys, trailing commas) and try again.
    Returns a parsed object or raises ValueError.
    """
    # 1. Strict parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Normalise unquoted keys:  word: → "word":
    fixed = re.sub(r'(\{|\,)\s*([A-Za-z_]\w*)\s*:', r'\1 "\2":', raw)
    # Remove trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        raise ValueError("Not parseable as JSON")


def _parse_entries(raw, default_subject):
    """
    Parse raw input into a list of dicts with keys:
      subject, log, diagnosis, chat_history
    Accepts:
      - JSON object  →  single entry
      - JSON array   →  multiple entries
      - plain text   →  blocks separated by blank lines, each becomes a log entry
    """
    raw = raw.strip()
    entries = []

    # Try JSON (strict + lenient) first
    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = _try_parse_json(raw)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    entries.append({
                        "subject":      str(item.get("subject", default_subject or "")),
                        "log":          str(item.get("log", item.get("body", item.get("message", "")))),
                        "diagnosis":    str(item.get("diagnosis", item.get("fix", item.get("resolution", "")))),
                        "chat_history": str(item.get("chat_history", item.get("history", ""))),
                    })
                return entries
        except (ValueError, TypeError):
            pass  # Fall through to plain-text parsing

    # Plain text — split on double newlines
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    for block in blocks:
        entries.append({
            "subject":      default_subject or "",
            "log":          block,
            "diagnosis":    "",
            "chat_history": "",
        })

    return entries


# ─── Routes ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    data = request.json or {}
    raw = data.get("raw", "").strip()
    default_subject = data.get("subject", "").strip()

    if not raw:
        return jsonify({"error": "No content provided"}), 400

    entries = _parse_entries(raw, default_subject)
    if not entries:
        return jsonify({"error": "Could not parse any entries from input"}), 400

    saved_ids = []
    errors = []
    for entry in entries:
        doc_id = _generate_id()
        try:
            save_or_update_incident(
                thread_id=doc_id,
                subject=entry["subject"],
                log=entry["log"],
                diagnosis=entry["diagnosis"],
                chat_history=entry["chat_history"],
                skip_embedding=False,
            )
            saved_ids.append(doc_id)
        except Exception as e:
            errors.append(f"ID {doc_id}: {e}")
        # Small delay so IDs don't collide
        time.sleep(0.05)

    result = {"saved": len(saved_ids), "ids": saved_ids}
    if errors:
        result["errors"] = errors

    return jsonify(result), 200


if __name__ == "__main__":
    port = int(os.getenv("UPLOAD_PORT", 5001))
    print(f"📥 Upload History server running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
