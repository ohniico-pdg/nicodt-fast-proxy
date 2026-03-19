from flask import Flask, request, jsonify
from flask_cors import CORS
import urllib.request
import urllib.error
import json
import os

app = Flask(__name__)
CORS(app, origins="*")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-2.0-flash"
GPT_MODEL    = "gpt-4o-mini"

def http_post(url, body, headers, timeout=120):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
            msg = err.get("error", {}).get("message") or str(err)
        except:
            msg = "HTTP " + str(e.code)
        raise Exception(msg)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "NicoDT_fast proxy"})

# ─── Claude : recherche web ───────────────────────────────────────────────────
@app.route("/api/claude/search", methods=["POST"])
def claude_search():
    data = request.get_json()
    key    = data.get("key", "")
    prompt = data.get("prompt", "")
    use_ws = data.get("web_search", True)

    if not key:
        return jsonify({"error": "Clé Claude manquante"}), 400

    system = (
        "Tu es un extracteur de données JSON. "
        "Réponds TOUJOURS avec un tableau JSON valide uniquement. "
        "Commence par [ et termine par ]. "
        "Aucune phrase d'introduction ni explication."
    )

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4000,
        "system": system,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["}
        ]
    }
    if use_ws:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01"
    }

    try:
        resp = http_post("https://api.anthropic.com/v1/messages", body, headers)
        for blk in resp.get("content", []):
            if blk.get("type") == "text":
                txt = blk["text"].strip()
                if not txt.startswith("["):
                    txt = "[" + txt
                return jsonify({"result": txt})
        return jsonify({"error": "Réponse Claude vide"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Claude : fiche captation ─────────────────────────────────────────────────
@app.route("/api/claude/fiche", methods=["POST"])
def claude_fiche():
    data   = request.get_json()
    key    = data.get("key", "")
    prompt = data.get("prompt", "")

    if not key:
        return jsonify({"error": "Clé Claude manquante"}), 400

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1800,
        "system": (
            "Tu es un coordinateur de production vidéo professionnel. "
            "Réponds en français de façon structurée et concrète."
        ),
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01"
    }

    try:
        resp = http_post("https://api.anthropic.com/v1/messages", body, headers)
        for blk in resp.get("content", []):
            if blk.get("type") == "text":
                return jsonify({"result": blk["text"]})
        return jsonify({"error": "Réponse Claude vide"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Gemini : fusion & scoring ────────────────────────────────────────────────
@app.route("/api/gemini/fusion", methods=["POST"])
def gemini_fusion():
    data   = request.get_json()
    key    = data.get("key", "")
    prompt = data.get("prompt", "")

    if not key:
        return jsonify({"error": "Clé Gemini manquante"}), 400

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.2}
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = http_post(url, body, headers)
        txt  = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not txt:
            return jsonify({"error": "Réponse Gemini vide"}), 500
        return jsonify({"result": txt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── GPT-4o : optimisation prompt ────────────────────────────────────────────
@app.route("/api/gpt/optimize", methods=["POST"])
def gpt_optimize():
    data     = request.get_json()
    key      = data.get("key", "")
    messages = data.get("messages", [])

    if not key:
        return jsonify({"error": "Clé OpenAI manquante"}), 400

    body = {
        "model": GPT_MODEL,
        "max_tokens": 200,
        "messages": messages
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key
    }

    try:
        resp = http_post("https://api.openai.com/v1/chat/completions", body, headers)
        txt  = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return jsonify({"result": txt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
