from flask import Flask, request, jsonify
import urllib.request
import urllib.error
import json
import os
import re
from datetime import datetime, timedelta

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

# ── Modèles ────────────────────────────────────────────────────────────────
GPT_MODEL    = "gpt-4o"
GPT_MINI     = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.5-flash"

# ── Fichiers JSON ──────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(__file__)
MEMORY_FILE  = os.path.join(BASE_DIR, "memory.json")
CACHE_FILE   = os.path.join(BASE_DIR, "cache.json")
ENGINE_FILE  = os.path.join(BASE_DIR, "engine_state.json")

# ── Helpers JSON ───────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def artist_key(name):
    """Normalise le nom d'artiste en clé JSON."""
    key = name.lower().strip()
    key = re.sub(r"[^a-z0-9\s]", "", key)
    key = re.sub(r"\s+", "_", key)
    return key

def guess_instagram(name):
    """Devine le handle Instagram depuis le nom."""
    clean = name.lower().strip()
    clean = re.sub(r"[^a-z0-9\s-]", "", clean)
    handle = clean.replace(" ", "").replace("-", "")
    return f"https://www.instagram.com/{handle}/"

# ── HTTP helper ────────────────────────────────────────────────────────────
def http_post(url, body, headers, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers=headers, method="POST"
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

# ── Extract JSON from text ─────────────────────────────────────────────────
def extract_json(raw):
    if not raw or not raw.strip():
        return []
    lo = raw.lower()
    if any(s in lo for s in ["aucun", "no event", "n'existe"]) and "[" not in raw:
        return []
    clean = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    s = clean.find("[")
    e = clean.rfind("]") + 1
    if s != -1 and e > s:
        try:
            return json.loads(clean[s:e])
        except:
            pass
    return []

# ── Engine state (pare-feu) ────────────────────────────────────────────────
def get_engine_state():
    state = load_json(ENGINE_FILE, {
        "active": True,
        "errors": 0,
        "paused_until": None,
        "last_run": None,
        "score": 0.5
    })
    if state.get("paused_until"):
        if datetime.now().isoformat() > state["paused_until"]:
            state["active"] = True
            state["errors"] = 0
            state["paused_until"] = None
            save_json(ENGINE_FILE, state)
    return state

def engine_error(state):
    state["errors"] = state.get("errors", 0) + 1
    state["score"] = max(0, state.get("score", 0.5) - 0.3)
    if state["errors"] >= 3:
        state["active"] = False
        pause_until = (datetime.now() + timedelta(hours=1)).isoformat()
        state["paused_until"] = pause_until
    save_json(ENGINE_FILE, state)
    return state

def engine_success(state):
    state["errors"] = 0
    state["score"] = min(1.0, state.get("score", 0.5) + 0.1)
    state["last_run"] = datetime.now().isoformat()
    save_json(ENGINE_FILE, state)
    return state

# ── Memory helpers ─────────────────────────────────────────────────────────
def get_artist_memory(artiste):
    memory = load_json(MEMORY_FILE, {})
    key = artist_key(artiste)
    return memory.get(key, {})

def save_artist_memory(artiste, data):
    memory = load_json(MEMORY_FILE, {})
    key = artist_key(artiste)
    if key not in memory:
        memory[key] = {}
    memory[key].update(data)
    memory[key]["updated_at"] = datetime.now().isoformat()
    save_json(MEMORY_FILE, memory)

# ── Cache 24h ──────────────────────────────────────────────────────────────
def get_cache(artiste):
    cache = load_json(CACHE_FILE, {})
    key = artist_key(artiste)
    entry = cache.get(key)
    if not entry:
        return None
    # Vérifie si < 24h
    cached_at = entry.get("cached_at", "")
    if cached_at:
        try:
            diff = datetime.now() - datetime.fromisoformat(cached_at)
            if diff.total_seconds() < 86400:
                return entry.get("results", [])
        except:
            pass
    return None

def save_cache(artiste, results):
    cache = load_json(CACHE_FILE, {})
    key = artist_key(artiste)
    cache[key] = {
        "cached_at": datetime.now().isoformat(),
        "results": results
    }
    save_json(CACHE_FILE, cache)

# ── GPT web search (Phase 1 & 2) ──────────────────────────────────────────
def gpt_search(key, prompt, timeout=90):
    system = (
        "Tu es un extracteur de données JSON pour la scène humoristique française. "
        "Réponds TOUJOURS avec un tableau JSON valide uniquement. "
        "Commence par [ et termine par ]. "
        "Uniquement des dates 2025-2026. "
        "Si rien trouvé: retourne []"
    )
    body = {
        "model": GPT_MODEL,
        "max_output_tokens": 3000,
        "tools": [{"type": "web_search_preview"}],
        "instructions": system,
        "input": prompt
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key
    }
    resp = http_post("https://api.openai.com/v1/responses", body, headers, timeout=timeout)
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    txt = c["text"].strip()
                    if not txt.startswith("["):
                        txt = "[" + txt
                    return txt
    return "[]"

# ── Gemini fusion (4 champs seulement) ────────────────────────────────────
def gemini_fusion(key, events, artiste):
    if not events:
        return events
    # Envoie seulement 4 champs légers à Gemini
    mini = [{"nom": e.get("nom",""), "date": e.get("date",""),
             "lieu": e.get("lieu",""), "source": e.get("source","")}
            for e in events]
    prompt = f"""Voici des événements pour "{artiste}":
{json.dumps(mini, ensure_ascii=False)}

1. Supprime les doublons (même date + lieu)
2. Trie par date croissante
3. Retourne uniquement les index des événements à GARDER (ex: [0,2,3])
Réponds UNIQUEMENT avec un tableau d'entiers: []"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0}
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    try:
        resp = http_post(url, body, headers, timeout=30)
        txt = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        # Extrait les index
        indexes = json.loads(txt.strip()) if txt.strip().startswith("[") else list(range(len(events)))
        return [events[i] for i in indexes if i < len(events)]
    except:
        return events

# ─────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    engine = get_engine_state()
    return jsonify({
        "status": "ok",
        "service": "NicoDT_fast proxy",
        "engine_active": engine.get("active", True),
        "engine_score": round(engine.get("score", 0.5), 2)
    })

# ── Recherche principale — pipeline 3 phases ──────────────────────────────
@app.route("/api/search", methods=["POST"])
def search():
    data     = request.get_json()
    artiste  = data.get("artiste", "").strip()
    filtres  = data.get("filtres", "")
    key_gpt  = data.get("key_gpt", "")
    key_gem  = data.get("key_gemini", "")

    if not artiste:
        return jsonify({"error": "Artiste manquant"}), 400
    if not key_gpt:
        return jsonify({"error": "Clé OpenAI manquante"}), 400

    # ── 1. Cache 24h ──────────────────────────────────────────────────────
    cached = get_cache(artiste)
    if cached is not None:
        return jsonify({
            "results": cached,
            "source": "cache",
            "message": "Résultats récents réutilisés — aucun appel API consommé"
        })

    # ── 2. Charge la mémoire de l'artiste ─────────────────────────────────
    mem = get_artist_memory(artiste)
    site_url   = mem.get("site")
    insta_url  = mem.get("instagram") or guess_instagram(artiste)
    top_sites  = mem.get("top_sites", ["fnacspectacles.com", "ticketmaster.fr"])
    echecs     = mem.get("echecs", 0)

    schema = f"""[{{"nom":"Nom exact du spectacle","artistes":"{artiste}","date":"JJ Mois AAAA","heure":"20h30","lieu":"Salle exacte, Ville","region":"Région","type":"Stand-up/Concert/Spectacle","jauge":"X places","prix_billets":"De X à Y EUR","site_officiel":"https://...","source":"URL exacte","contact_production":"email ou tel","description":"2-3 phrases","score_fiabilite":"eleve/moyen/faible"}}]"""

    events = []
    source_used = []

    # ── Phase 1 : Site officiel ────────────────────────────────────────────
    if site_url:
        prompt = f"""Va sur {site_url} et trouve les dates de spectacle de "{artiste}" en France.
Filtres: {filtres or 'aucun'}
Cherche la page "dates", "tournée", "spectacles", "agenda" du site.
Pour chaque date: salle exacte, capacité, prix billets, URL.
Uniquement dates 2025-2026. Si rien: retourne [].
Réponds UNIQUEMENT avec ce JSON:\n{schema}"""
    else:
        prompt = f"""Recherche Google: "{artiste} humoriste site officiel dates spectacle 2026"
Trouve son site officiel puis sa page de dates/tournée.
Pour chaque date: salle exacte, capacité, prix billets, URL source exacte.
Uniquement dates 2025-2026. Si rien: retourne [].
Réponds UNIQUEMENT avec ce JSON:\n{schema}"""

    try:
        raw = gpt_search(key_gpt, prompt, timeout=60)
        found = extract_json(raw)
        events.extend(found)
        source_used.append("site_officiel")
        if len(events) >= 2:
            # STOP — on a assez
            events = gemini_fusion(key_gem, events, artiste) if key_gem and len(events) > 2 else events
            save_cache(artiste, events)
            return jsonify({"results": events, "source": "site_officiel", "sources_used": source_used})
    except Exception as e:
        print(f"Phase 1 error: {e}", flush=True)

    # ── Phase 2 : Instagram ────────────────────────────────────────────────
    prompt2 = f"""Va sur {insta_url}
Lis la bio Instagram de "{artiste}".
1. S'il y a un lien Linktree ou site dans la bio, suis-le et cherche les dates de spectacle
2. Lis les 5 derniers posts pour des annonces de dates
Pour chaque date trouvée: salle, ville, date, prix si disponible.
Uniquement dates 2025-2026. Si rien: retourne [].
Réponds UNIQUEMENT avec ce JSON:\n{schema}"""

    try:
        raw2 = gpt_search(key_gpt, prompt2, timeout=60)
        found2 = extract_json(raw2)
        events.extend(found2)
        source_used.append("instagram")
        if len(events) >= 2:
            events = gemini_fusion(key_gem, events, artiste) if key_gem and len(events) > 2 else events
            save_cache(artiste, events)
            return jsonify({"results": events, "source": "instagram", "sources_used": source_used})
    except Exception as e:
        print(f"Phase 2 error: {e}", flush=True)

    # ── Phase 3 : Billetterie (dernier recours) ────────────────────────────
    sites_str = "\n".join(f"- {s}" for s in top_sites)
    prompt3 = f"""Recherche les dates de spectacle/stand-up de "{artiste}" en France sur:
{sites_str}
Filtres: {filtres or 'aucun'}
Pour chaque date: salle exacte, capacité, prix billets, URL exacte de la page.
Uniquement dates 2025-2026. Si rien: retourne [].
Réponds UNIQUEMENT avec ce JSON:\n{schema}"""

    try:
        raw3 = gpt_search(key_gpt, prompt3, timeout=90)
        found3 = extract_json(raw3)
        events.extend(found3)
        source_used.append("billetterie")
    except Exception as e:
        print(f"Phase 3 error: {e}", flush=True)

    # ── Fusion Gemini (4 champs) ───────────────────────────────────────────
    if key_gem and len(events) > 2:
        events = gemini_fusion(key_gem, events, artiste)

    # ── Gestion échecs ─────────────────────────────────────────────────────
    if not events:
        echecs_new = echecs + 1
        save_artist_memory(artiste, {"echecs": echecs_new})
        suggestion = ""
        if echecs_new >= 3:
            suggestion = f"Essayez avec le nom complet ou vérifiez l'orthographe de '{artiste}'"
        return jsonify({
            "results": [],
            "sources_used": source_used,
            "echecs": echecs_new,
            "suggestion": suggestion
        })

    save_cache(artiste, events)
    return jsonify({"results": events, "sources_used": source_used})

# ── Valider un événement ───────────────────────────────────────────────────
@app.route("/api/validate", methods=["POST"])
def validate_event():
    data    = request.get_json()
    artiste = data.get("artiste", "").strip()
    event   = data.get("event", {})
    site    = data.get("site_officiel", "")
    insta   = data.get("instagram", "")
    source  = data.get("source", "")

    if not artiste or not event:
        return jsonify({"error": "Données manquantes"}), 400

    mem = get_artist_memory(artiste)

    # Mémorise site + Instagram si trouvés
    update = {"echecs": 0, "validations": mem.get("validations", 0) + 1}
    if site:
        update["site"] = site
    if insta:
        update["instagram"] = insta

    # Mémorise la salle si jauge connue
    if event.get("lieu") and event.get("jauge"):
        salles = mem.get("salles_connues", {})
        nom_salle = event["lieu"].split(",")[0].strip()
        salles[nom_salle] = {
            "jauge": event.get("jauge"),
            "contact": event.get("contact_production", "")
        }
        update["salles_connues"] = salles

    # Met à jour top_sites si source connue
    if source:
        domain = source.split("/")[2] if source.startswith("http") else source
        top = mem.get("top_sites", [])
        if domain and domain not in top:
            top.insert(0, domain)
            update["top_sites"] = top[:3]

    save_artist_memory(artiste, update)

    # Log pour le moteur de nuit
    log = load_json(os.path.join(BASE_DIR, "day_log.json"), {"date": "", "validations": []})
    today = datetime.now().strftime("%Y-%m-%d")
    if log.get("date") != today:
        log = {"date": today, "validations": []}
    log["validations"].append({
        "artiste": artiste,
        "event": event.get("nom", ""),
        "at": datetime.now().isoformat()
    })
    save_json(os.path.join(BASE_DIR, "day_log.json"), log)

    return jsonify({"saved": True, "validations": update["validations"]})

# ── Jauge salle (recherche légère) ────────────────────────────────────────
@app.route("/api/jauge", methods=["POST"])
def get_jauge():
    data     = request.get_json()
    salle    = data.get("salle", "").strip()
    ville    = data.get("ville", "").strip()
    key_gpt  = data.get("key_gpt", "")

    if not salle or not key_gpt:
        return jsonify({"error": "Salle ou clé manquante"}), 400

    # Vérifie d'abord en mémoire
    memory = load_json(MEMORY_FILE, {})
    for art_data in memory.values():
        salles = art_data.get("salles_connues", {})
        if salle in salles:
            return jsonify({"jauge": salles[salle].get("jauge"), "source": "memoire"})

    # Sinon recherche GPT mini légère
    prompt = f'Quelle est la capacité (jauge, nombre de places) de la salle "{salle}" à {ville} ? Réponds juste avec le nombre.'
    body = {
        "model": GPT_MINI,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key_gpt}
    try:
        resp = http_post("https://api.openai.com/v1/chat/completions", body, headers, timeout=15)
        txt = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        numbers = re.findall(r"\d+", txt.replace(" ", "").replace("\xa0", ""))
        jauge = numbers[0] if numbers else None
        return jsonify({"jauge": jauge, "source": "recherche"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Fiche matériel tournage ────────────────────────────────────────────────
@app.route("/api/fiche", methods=["POST"])
def generate_fiche():
    data    = request.get_json()
    event   = data.get("event", {})
    key_gpt = data.get("key_gpt", "")
    key_c   = data.get("key_claude", "")

    if not event:
        return jsonify({"error": "Événement manquant"}), 400

    prompt = f"""Tu es coordinateur de production vidéo professionnel pour captation de spectacles d'humour.
Génère une fiche technique de tournage pour :

SPECTACLE : {event.get('nom','')}
ARTISTE   : {event.get('artistes','')}
DATE      : {event.get('date','')} {event.get('heure','')}
SALLE     : {event.get('lieu','')} | Jauge : {event.get('jauge','inconnue')}
PRIX      : {event.get('prix_billets','N/A')}

Structure en 6 sections courtes et concrètes :
1. INFOS CLÉS (date, heure, salle, accès)
2. MATÉRIEL CAMÉRA (modèles recommandés, nombre, positions)
3. SON (micro, régie, contraintes salle)
4. ÉCLAIRAGE (type de lumière scène, contraintes)
5. ÉQUIPE (nombre de personnes, rôles)
6. CHECKLIST (8 points avant tournage)

Sois très concret. Pas de blabla."""

    # Essaie Claude d'abord, sinon GPT
    if key_c:
        try:
            body = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1200,
                "system": "Tu es coordinateur de production vidéo. Réponds en français, de façon concise et pratique.",
                "messages": [{"role": "user", "content": prompt}]
            }
            headers = {"Content-Type": "application/json", "x-api-key": key_c, "anthropic-version": "2023-06-01"}
            resp = http_post("https://api.anthropic.com/v1/messages", body, headers, timeout=30)
            for blk in resp.get("content", []):
                if blk.get("type") == "text":
                    return jsonify({"fiche": blk["text"]})
        except:
            pass

    if key_gpt:
        body = {
            "model": GPT_MINI,
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key_gpt}
        try:
            resp = http_post("https://api.openai.com/v1/chat/completions", body, headers, timeout=30)
            txt = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            return jsonify({"fiche": txt})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "Aucune clé disponible"}), 400

# ── Moteur de nuit — déclenché à 00h00 ───────────────────────────────────
@app.route("/api/night-engine", methods=["POST"])
def night_engine():
    data    = request.get_json()
    key_gpt = data.get("key_gpt", "")
    secret  = data.get("secret", "")

    # Vérif basique anti-abus
    if secret != os.environ.get("NIGHT_SECRET", "capta2026"):
        return jsonify({"error": "Non autorisé"}), 403

    # Vérifie l'état du pare-feu
    engine_state = get_engine_state()
    if not engine_state.get("active", True):
        return jsonify({"skipped": True, "reason": "Circuit breaker actif"})

    # Vérifie activité du jour (règle 15 min)
    log = load_json(os.path.join(BASE_DIR, "day_log.json"), {"date": "", "validations": []})
    today = datetime.now().strftime("%Y-%m-%d")
    if log.get("date") != today or not log.get("validations"):
        return jsonify({"skipped": True, "reason": "Aucune activité aujourd'hui"})

    validations = log.get("validations", [])
    artistes_du_jour = list(set(v["artiste"] for v in validations))

    if not key_gpt:
        return jsonify({"skipped": True, "reason": "Clé GPT manquante"})

    # Pour chaque artiste validé aujourd'hui — améliore son prompt
    memory = load_json(MEMORY_FILE, {})
    improved = []

    for artiste in artistes_du_jour[:5]:  # Max 5 par nuit
        try:
            key = artist_key(artiste)
            mem = memory.get(key, {})
            validations_count = mem.get("validations", 0)

            prompt = f"""Artiste: "{artiste}"
Données mémorisées: site={mem.get('site','?')}, instagram={mem.get('instagram','?')}, validations={validations_count}
Sources fiables: {mem.get('top_sites', [])}

En 1 phrase max 15 mots: UNE amélioration concrète pour la prochaine recherche de cet artiste."""

            body = {
                "model": GPT_MINI,
                "max_tokens": 60,
                "messages": [{"role": "user", "content": prompt}]
            }
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key_gpt}
            resp = http_post("https://api.openai.com/v1/chat/completions", body, headers, timeout=10)
            note = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            if note:
                memory[key]["optim_note"] = note
                improved.append(artiste)

        except Exception as e:
            engine_error(engine_state)
            print(f"Night engine error for {artiste}: {e}", flush=True)
            continue

    save_json(MEMORY_FILE, memory)
    engine_success(engine_state)

    return jsonify({
        "run": True,
        "artistes_improved": improved,
        "total": len(improved)
    })

# ── État du système ────────────────────────────────────────────────────────
@app.route("/api/status", methods=["GET"])
def status():
    engine  = get_engine_state()
    memory  = load_json(MEMORY_FILE, {})
    log     = load_json(os.path.join(BASE_DIR, "day_log.json"), {})
    today   = datetime.now().strftime("%Y-%m-%d")

    return jsonify({
        "engine": {
            "active": engine.get("active", True),
            "score": round(engine.get("score", 0.5), 2),
            "errors": engine.get("errors", 0),
            "paused_until": engine.get("paused_until"),
            "last_run": engine.get("last_run")
        },
        "memory": {
            "artistes_memorises": len(memory),
            "noms": list(memory.keys())[:10]
        },
        "today": {
            "validations": len(log.get("validations", [])) if log.get("date") == today else 0
        }
    })

# ── Mémoire artiste ────────────────────────────────────────────────────────
@app.route("/api/memory/<artiste_name>", methods=["GET"])
def get_memory(artiste_name):
    mem = get_artist_memory(artiste_name)
    return jsonify(mem)

# ── Mémoire tous artistes ─────────────────────────────────────────────────
@app.route("/api/memory/all", methods=["GET"])
def all_memory():
    memory = load_json(MEMORY_FILE, {})
    result = []
    for key, data in memory.items():
        result.append({
            "key": key,
            "nom": key.replace("_", " ").title(),
            "site": data.get("site", ""),
            "instagram": data.get("instagram", ""),
            "validations": data.get("validations", 0),
            "echecs": data.get("echecs", 0),
            "top_sites": data.get("top_sites", []),
            "updated_at": data.get("updated_at", ""),
            "optim_note": data.get("optim_note", "")
        })
    result.sort(key=lambda x: x["validations"], reverse=True)
    return jsonify({"artistes": result, "total": len(result)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
