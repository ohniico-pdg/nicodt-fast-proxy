from flask import Flask, request, jsonify
import urllib.request
import urllib.error
import json
import os

app = Flask(__name__)

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    from flask import Response
    r = Response()
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return r, 200

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-2.5-flash"
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
    """Recherche via GPT-4o avec web_search_preview (fallback quand Claude quota épuisé)."""
    data = request.get_json()
    # Accepte clé Claude OU clé OpenAI (on utilise OpenAI ici)
    claude_key = data.get("key", "")
    openai_key = data.get("openai_key", "")
    prompt = data.get("prompt", "")

    # Utilise openai_key si fourni, sinon essaie claude_key comme fallback
    key = openai_key or claude_key
    if not key:
        return jsonify({"error": "Aucune clé API disponible"}), 400

    system = (
        "Tu es un extracteur de données JSON. "
        "Réponds TOUJOURS avec un tableau JSON valide uniquement. "
        "Commence par [ et termine par ]. "
        "Aucune phrase d'introduction ni explication."
    )

    body = {
        "model": "gpt-4o",
        "max_output_tokens": 4000,
        "tools": [{"type": "web_search_preview"}],
        "instructions": system,
        "input": prompt
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key
    }

    try:
        resp = http_post("https://api.openai.com/v1/responses", body, headers, timeout=120)
        print("GPT SEARCH RESP:", str(resp)[:300], flush=True)
        for item in resp.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        txt = c["text"].strip()
                        if not txt.startswith("["):
                            txt = "[" + txt
                        return jsonify({"result": txt})
        return jsonify({"error": "Réponse GPT vide"}), 500
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
        f"{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.2}
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key
    }

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
