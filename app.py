from flask import Flask, request, jsonify
import urllib.request
import urllib.error
import json
import os
import re
import unicodedata
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
GPT_MODEL  = "gpt-4o"
GPT_MINI   = "gpt-4o-mini"

# ── Fichiers JSON ──────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")
CACHE_FILE  = os.path.join(BASE_DIR, "cache.json")
ENGINE_FILE = os.path.join(BASE_DIR, "engine_state.json")
DAY_LOG     = os.path.join(BASE_DIR, "day_log.json")

# ── Clés API depuis variables d'environnement ──────────────────────────────
def get_key_gpt():    return os.environ.get("OPENAI_API_KEY", "")
def get_key_gemini(): return os.environ.get("GEMINI_API_KEY", "")
def get_key_claude(): return os.environ.get("ANTHROPIC_API_KEY", "")

# ── JSON helpers ───────────────────────────────────────────────────────────
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
    k = name.lower().strip()
    k = re.sub(r"[^a-z0-9\s]", "", k)
    return re.sub(r"\s+", "_", k)

# ── Instagram variants ─────────────────────────────────────────────────────
def normalize_name(name):
    """Supprime accents et caractères spéciaux."""
    n = unicodedata.normalize("NFKD", name.lower().strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", "", n).strip()

def guess_instagram_variants(name):
    """Génère les 3 variantes de handle Instagram."""
    clean = normalize_name(name)
    parts = clean.split()
    if len(parts) >= 2:
        return [
            "https://www.instagram.com/" + "".join(parts) + "/",
            "https://www.instagram.com/" + ".".join(parts) + "/",
            "https://www.instagram.com/" + "_".join(parts) + "/",
        ]
    elif parts:
        return [f"https://www.instagram.com/{parts[0]}/"]
    return []

def guess_linktree_variants(name):
    """Génère les variantes de liens bio (Linktree + alternatives)."""
    clean = normalize_name(name)
    parts = clean.split()
    if not parts:
        return []
    joined   = "".join(parts)
    hyphen   = "-".join(parts)
    dot      = ".".join(parts)
    underscore = "_".join(parts)
    variants = []
    for base in ["https://linktr.ee/", "https://msha.ke/", "https://bio.link/", "https://beacons.ai/"]:
        for suffix in [joined, hyphen, dot, underscore]:
            variants.append(base + suffix)
    return variants[:8]  # Max 8 variantes

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

# ── Extract JSON ───────────────────────────────────────────────────────────
def extract_json(raw):
    if not raw or not raw.strip():
        return []
    lo = raw.lower()
    if any(s in lo for s in ["aucun", "no event", "n'existe", "introuvable"]) and "[" not in raw:
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

# ── NOUVEAU : Déduplication locale Python — remplace Gemini ───────────────
def deduplicate_local(events):
    """Déduplique par date+lieu sans aucun appel API."""
    seen = set()
    result = []
    for ev in events:
        date = (ev.get("date") or "").strip().lower()
        lieu = (ev.get("lieu") or "").split(",")[0].strip().lower()
        key = f"{date}|{lieu}"
        if key not in seen:
            seen.add(key)
            result.append(ev)
    # Tri par date
    def sort_key(ev):
        d = ev.get("date", "")
        try:
            parts = d.split()
            mois = {"janvier":1,"février":2,"fevrier":2,"mars":3,"avril":4,
                    "mai":5,"juin":6,"juillet":7,"août":8,"aout":8,
                    "septembre":9,"octobre":10,"novembre":11,"décembre":12,"decembre":12,
                    "jan":1,"fév":2,"fev":2,"mar":3,"avr":4,"jun":6,
                    "juil":7,"aoû":8,"sep":9,"oct":10,"nov":11,"déc":12,"dec":12}
            if len(parts) >= 3:
                return (int(parts[2]), mois.get(parts[1].lower(), 0), int(parts[0]))
        except:
            pass
        return (9999, 0, 0)
    result.sort(key=sort_key)
    return result

# ── NOUVEAU : Score sources local — remplace moteur de nuit GPT ────────────
def update_source_scores(artiste, sources_used, found_results):
    """Met à jour le score des sources sans aucun appel API."""
    mem = load_json(MEMORY_FILE, {})
    key = artist_key(artiste)
    if key not in mem:
        mem[key] = {}
    scores = mem[key].get("source_scores", {})
    for source in sources_used:
        current = scores.get(source, 0.5)
        if found_results:
            scores[source] = min(1.0, current + 0.1)
        else:
            scores[source] = max(0.0, current - 0.05)
    mem[key]["source_scores"] = scores
    mem[key]["updated_at"] = datetime.now().isoformat()
    save_json(MEMORY_FILE, mem)

# ── NOUVEAU : Injection mémoire dans le prompt GPT ─────────────────────────
def build_context_from_memory(mem, artiste):
    """Construit le contexte mémoire à injecter dans le prompt GPT."""
    lines = []
    if mem.get("site"):
        lines.append(f"- Site officiel confirmé : {mem['site']}")
    if mem.get("instagram"):
        lines.append(f"- Instagram confirmé : {mem['instagram']}")
    scores = mem.get("source_scores", {})
    top_sources = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top_sources:
        top_str = ", ".join(f"{s} (score {round(v,1)})" for s, v in top_sources[:3])
        lines.append(f"- Sources les plus fiables pour cet artiste : {top_str}")
    ev_confirmes = mem.get("evenements_confirmes", [])
    if ev_confirmes:
        recent = ev_confirmes[-2:]
        for ev in recent:
            lines.append(f"- Événement déjà confirmé : {ev.get('date','')} · {ev.get('salle','')} · {ev.get('adresse','')}")
    if not lines:
        return ""
    return "CONTEXTE MÉMOIRE (données déjà confirmées — ne pas re-chercher) :\n" + "\n".join(lines)

# ── Engine state ───────────────────────────────────────────────────────────
def get_engine_state():
    state = load_json(ENGINE_FILE, {"active": True, "errors": 0, "paused_until": None, "score": 0.8})
    if state.get("paused_until"):
        if datetime.now().isoformat() > state["paused_until"]:
            state.update({"active": True, "errors": 0, "paused_until": None})
            save_json(ENGINE_FILE, state)
    return state

def engine_error(state):
    state["errors"] = state.get("errors", 0) + 1
    state["score"] = max(0, state.get("score", 0.8) - 0.3)
    if state["errors"] >= 3:
        state["active"] = False
        state["paused_until"] = (datetime.now() + timedelta(hours=1)).isoformat()
    save_json(ENGINE_FILE, state)
    return state

def engine_success(state):
    state["errors"] = 0
    state["score"] = min(1.0, state.get("score", 0.8) + 0.05)
    state["last_run"] = datetime.now().isoformat()
    save_json(ENGINE_FILE, state)
    return state

# ── Memory helpers ─────────────────────────────────────────────────────────
def get_artist_memory(artiste):
    return load_json(MEMORY_FILE, {}).get(artist_key(artiste), {})

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
    entry = load_json(CACHE_FILE, {}).get(artist_key(artiste))
    if not entry:
        return None
    try:
        diff = datetime.now() - datetime.fromisoformat(entry.get("cached_at", ""))
        if diff.total_seconds() < 86400:
            return entry.get("results", [])
    except:
        pass
    return None

def save_cache(artiste, results):
    cache = load_json(CACHE_FILE, {})
    cache[artist_key(artiste)] = {"cached_at": datetime.now().isoformat(), "results": results}
    save_json(CACHE_FILE, cache)

# ── GPT web search ─────────────────────────────────────────────────────────
def gpt_search(key, prompt, timeout=90):
    system = (
        "Tu es un expert en recherche d'événements culturels français (spectacles, stand-up, humour). "
        "Tu retournes TOUJOURS un tableau JSON valide commençant par [ et finissant par ]. "
        "Les résultats sont DÉDUPLIQUÉS et TRIÉS par date croissante. "
        "Uniquement des dates 2025-2026. Si rien trouvé: []"
    )
    body = {
        "model": GPT_MODEL,
        "max_output_tokens": 3000,
        "tools": [{"type": "web_search_preview"}],
        "instructions": system,
        "input": prompt
    }
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
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

# ── JSON Schema événement ──────────────────────────────────────────────────
EVENT_SCHEMA = '[{"nom":"Nom exact du spectacle","artistes":"Nom artiste","date":"JJ Mois AAAA","heure":"20h30","lieu":"Salle exacte, Ville","region":"Région","type":"Stand-up/Concert/Spectacle","jauge":"1500 places (IMPORTANT: mettre le nombre exact de places de la salle, ex: 500 places, 2000 places)","prix_billets":"De X à Y EUR","site_officiel":"https://...","source":"URL exacte de la page","contact_production":"email ou tel si disponible","description":"2 phrases max","score_fiabilite":"eleve/moyen/faible"}]'

# ─────────────────────────────────────────────────────────────────────────
# ROUTE PRINCIPALE — nouvelle architecture 5 phases
# ─────────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    engine = get_engine_state()
    return jsonify({
        "status": "ok",
        "service": "Capta Vidéo proxy",
        "engine_active": engine.get("active", True),
        "engine_score": round(engine.get("score", 0.8), 2),
        "architecture": "v5 — memory injection + local dedup"
    })

@app.route("/api/search", methods=["POST"])
def search():
    data    = request.get_json()
    artiste = data.get("artiste", "").strip()
    filtres = data.get("filtres", "")
    key_gpt = data.get("key_gpt", "") or get_key_gpt()
    ig_input = data.get("instagram", "").strip().replace("@", "")

    if not artiste:
        return jsonify({"error": "Artiste manquant"}), 400
    if not key_gpt:
        return jsonify({"error": "Clé OpenAI manquante — configurez OPENAI_API_KEY sur Render"}), 400

    # ══════════════════════════════════════════════════════════
    # PHASE 0 — Mémoire & Cache
    # ══════════════════════════════════════════════════════════
    mem = get_artist_memory(artiste)
    cached = get_cache(artiste)
    if cached is not None:
        return jsonify({"results": cached, "source": "cache", "sources_used": ["cache"],
                        "message": "Résultats récents réutilisés — aucun appel API consommé"})

    # Injection contexte mémoire dans le prompt
    context = build_context_from_memory(mem, artiste)

    events      = []
    sources_used = []

    # ══════════════════════════════════════════════════════════
    # PHASE 1 — Site officiel
    # ══════════════════════════════════════════════════════════
    site_url = mem.get("site")
    if site_url:
        prompt1 = f"""{context}

Va sur {site_url} et cherche la page "dates", "tournée", "spectacles" ou "agenda".
Artiste : "{artiste}" | Filtres : {filtres or 'aucun'}
Extrait TOUTES les dates de spectacle 2025-2026.
Pour chaque date : salle exacte, capacité/jauge, prix billets, URL exacte.
Résultats DÉDUPLIQUÉS et TRIÉS par date croissante.
Réponds UNIQUEMENT avec ce JSON : {EVENT_SCHEMA}"""
    else:
        prompt1 = f"""{context}

Recherche : "{artiste} humoriste site officiel dates spectacle tournée 2026"
1. Trouve son site officiel
2. Va sur sa page dates/tournée
3. Extrait TOUTES les dates de spectacle 2025-2026
Pour chaque date : salle exacte, capacité/jauge, prix billets, URL exacte source.
Résultats DÉDUPLIQUÉS et TRIÉS par date croissante.
Réponds UNIQUEMENT avec ce JSON : {EVENT_SCHEMA}"""

    try:
        raw1 = gpt_search(key_gpt, prompt1, timeout=60)
        found1 = extract_json(raw1)
        if found1:
            # Mémorise le site si trouvé
            for ev in found1:
                if ev.get("site_officiel"):
                    save_artist_memory(artiste, {"site": ev["site_officiel"]})
                    break
        events.extend(found1)
        sources_used.append("site_officiel")
        if len(events) >= 2:
            events = deduplicate_local(events)
            update_source_scores(artiste, sources_used, True)
            save_cache(artiste, events)
            return jsonify({"results": events, "source": "site_officiel", "sources_used": sources_used})
    except Exception as e:
        print(f"Phase 1 error: {e}", flush=True)

    # ══════════════════════════════════════════════════════════
    # PHASE 2 — Instagram optionnel — handle = indice, pas restriction
    # ══════════════════════════════════════════════════════════
    auto_variants = guess_instagram_variants(artiste)
    if ig_input:
        hint_url = f"https://www.instagram.com/{ig_input}/"
        ig_variants = [hint_url] + [v for v in auto_variants if v != hint_url]
    elif mem.get("instagram"):
        mem_url = mem.get("instagram")
        ig_variants = [mem_url] + [v for v in auto_variants if v != mem_url]
    else:
        ig_variants = auto_variants

    linktree_variants = guess_linktree_variants(artiste)
    linktree_str = "\n".join(f"- {u}" for u in linktree_variants[:6])

    found2 = []
    working_ig = None
    for ig_url in ig_variants:
        prompt2 = f"""{context}

Va sur {ig_url}
Vérifie que ce profil Instagram appartient bien à l'artiste "{artiste}" (humoriste/comédien français).
Si ce n'est pas le bon profil : retourne [].

Si c'est le bon profil :
1. Lis la bio — cherche un lien externe (Linktree, site officiel, billetterie)
   Si tu trouves un lien dans la bio, suis-le et cherche les dates de spectacle.
   
2. Teste aussi ces liens bio possibles dans l'ordre (arrête dès qu'un fonctionne) :
{linktree_str}

3. Lis les 6 derniers posts et stories épinglées pour des annonces de dates.

Pour chaque date trouvée : salle, ville, date, heure, prix si disponible.
Uniquement dates 2025-2026. Si rien : retourne [].
Résultats DÉDUPLIQUÉS. Réponds UNIQUEMENT avec ce JSON : {EVENT_SCHEMA}"""

        try:
            raw2 = gpt_search(key_gpt, prompt2, timeout=70)
            found2 = extract_json(raw2)
            if found2:
                working_ig = ig_url
                break
        except Exception as e:
            print(f"Phase 2 variant {ig_url} error: {e}", flush=True)
            continue

    if working_ig:
        save_artist_memory(artiste, {"instagram": working_ig})

    events.extend(found2)
    sources_used.append("instagram")
    if len(events) >= 2:
        events = deduplicate_local(events)
        update_source_scores(artiste, sources_used, True)
        save_cache(artiste, events)
        return jsonify({"results": events, "source": "instagram", "sources_used": sources_used})

    # ══════════════════════════════════════════════════════════
    # PHASE 3 — Billetterie (dernier recours)
    # ══════════════════════════════════════════════════════════
    scores = mem.get("source_scores", {})
    default_sources = ["fnacspectacles.com", "ticketmaster.fr", "infoconcert.com", "billetreduc.com"]
    sorted_sources = sorted(default_sources, key=lambda s: scores.get(s, 0.5), reverse=True)
    sites_str = "\n".join(f"- {s}" for s in sorted_sources[:3])

    prompt3 = f"""{context}

Recherche les dates de spectacle de "{artiste}" en France sur ces sites (dans l'ordre) :
{sites_str}

Filtres : {filtres or 'aucun'}
Pour chaque date : salle exacte, capacité/jauge, prix billets, URL exacte de la page billet.
Uniquement dates 2025-2026. Si rien : retourne [].
Résultats DÉDUPLIQUÉS et TRIÉS par date.
Réponds UNIQUEMENT avec ce JSON : {EVENT_SCHEMA}"""

    try:
        raw3 = gpt_search(key_gpt, prompt3, timeout=90)
        found3 = extract_json(raw3)
        events.extend(found3)
        sources_used.append("billetterie")
    except Exception as e:
        print(f"Phase 3 error: {e}", flush=True)

    # ══════════════════════════════════════════════════════════
    # PHASE 4 — Déduplication locale + score sources
    # ══════════════════════════════════════════════════════════
    events = deduplicate_local(events)
    update_source_scores(artiste, sources_used, len(events) > 0)

    if not events:
        mem_echecs = get_artist_memory(artiste)
        echecs = mem_echecs.get("echecs", 0) + 1
        save_artist_memory(artiste, {"echecs": echecs})
        suggestion = ""
        if echecs >= 3:
            suggestion = f"Vérifiez l'orthographe de '{artiste}' ou essayez son nom de scène"
        return jsonify({"results": [], "sources_used": sources_used, "echecs": echecs, "suggestion": suggestion})

    save_cache(artiste, events)
    return jsonify({"results": events, "sources_used": sources_used})


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
    update = {"echecs": 0, "validations": mem.get("validations", 0) + 1}
    if site:   update["site"] = site
    if insta:  update["instagram"] = insta

    # Scores sources
    if source:
        domain = source.split("/")[2] if source.startswith("http") else source
        scores = mem.get("source_scores", {})
        scores[domain] = min(1.0, scores.get(domain, 0.5) + 0.15)
        update["source_scores"] = scores

    save_artist_memory(artiste, update)

    # Log journalier
    log = load_json(DAY_LOG, {"date": "", "validations": []})
    today = datetime.now().strftime("%Y-%m-%d")
    if log.get("date") != today:
        log = {"date": today, "validations": []}
    log["validations"].append({"artiste": artiste, "event": event.get("nom", ""), "at": datetime.now().isoformat()})
    save_json(DAY_LOG, log)

    return jsonify({"saved": True, "validations": update["validations"]})


# ── Confirmer le lieu d'un événement ──────────────────────────────────────
@app.route("/api/confirm-lieu", methods=["POST"])
def confirm_lieu():
    data    = request.get_json()
    artiste = data.get("artiste", "").strip()
    event   = data.get("event", {})

    if not artiste or not event:
        return jsonify({"error": "Données manquantes"}), 400

    mem = get_artist_memory(artiste)
    ev_confirmes = mem.get("evenements_confirmes", [])

    # Vérifie si cet événement est déjà confirmé
    date_ev  = event.get("date", "")
    salle_ev = event.get("lieu", "").split(",")[0].strip()
    already  = any(e.get("date") == date_ev and e.get("salle") == salle_ev for e in ev_confirmes)

    if not already:
        ev_confirmes.append({
            "date":       date_ev,
            "salle":      salle_ev,
            "adresse":    event.get("lieu", ""),
            "jauge":      event.get("jauge", ""),
            "prix":       event.get("prix_billets", ""),
            "contact":    event.get("contact_production", ""),
            "source":     event.get("source", ""),
            "confirme_le": datetime.now().isoformat()
        })
        save_artist_memory(artiste, {"evenements_confirmes": ev_confirmes})

    return jsonify({"confirmed": True, "total_confirmes": len(ev_confirmes)})


# ── Jauge salle ────────────────────────────────────────────────────────────
@app.route("/api/jauge", methods=["POST"])
def get_jauge():
    data    = request.get_json()
    salle   = data.get("salle", "").strip()
    ville   = data.get("ville", "").strip()
    key_gpt = data.get("key_gpt", "") or get_key_gpt()

    if not salle or not key_gpt:
        return jsonify({"error": "Salle manquante ou clé OpenAI non configurée"}), 400

    memory = load_json(MEMORY_FILE, {})
    for art_data in memory.values():
        for ev in art_data.get("evenements_confirmes", []):
            if salle.lower() in ev.get("salle", "").lower() and ev.get("jauge"):
                return jsonify({"jauge": ev["jauge"], "source": "memoire_confirmee"})

    prompt = f'Quelle est la capacité exacte (nombre de places) de la salle "{salle}" à {ville} en France ? Réponds uniquement avec le nombre.'
    body = {
        "model": GPT_MINI,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key_gpt}
    try:
        resp = http_post("https://api.openai.com/v1/chat/completions", body, headers, timeout=15)
        txt  = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        nums = re.findall(r"\d+", txt.replace(" ", "").replace("\xa0", ""))
        return jsonify({"jauge": nums[0] if nums else None, "source": "recherche"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Fiche matériel tournage ────────────────────────────────────────────────
@app.route("/api/fiche", methods=["POST"])
def generate_fiche():
    data    = request.get_json()
    event   = data.get("event", {})
    key_gpt = data.get("key_gpt", "") or get_key_gpt()
    key_c   = data.get("key_claude", "") or get_key_claude()

    if not event:
        return jsonify({"error": "Événement manquant"}), 400

    prompt = f"""Tu es coordinateur de production vidéo professionnel pour captation de spectacles d'humour en France.
Génère une fiche technique de tournage concise pour :

SPECTACLE : {event.get('nom', '')}
ARTISTE   : {event.get('artistes', '')}
DATE      : {event.get('date', '')} {event.get('heure', '')}
SALLE     : {event.get('lieu', '')} | Jauge : {event.get('jauge', 'inconnue')}
PRIX      : {event.get('prix_billets', 'N/A')}

Structure en 6 sections pratiques :
1. INFOS CLÉS (date, heure, salle, adresse, accès)
2. MATÉRIEL CAMÉRA (modèles recommandés, nombre, positions selon jauge)
3. SON (micro, régie, contraintes salle)
4. ÉCLAIRAGE (type lumière scène, contraintes)
5. ÉQUIPE (nombre personnes, rôles)
6. CHECKLIST AVANT TOURNAGE (8 points)

Sois très concret et adapté à la jauge de la salle."""

    if key_c:
        try:
            body = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1200,
                "system": "Tu es coordinateur de production vidéo. Réponds en français, concis et pratique.",
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
            txt  = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            return jsonify({"fiche": txt})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "Aucune clé disponible"}), 400


# ── Moteur de nuit — calcul local uniquement, 0 token ─────────────────────
@app.route("/api/night-engine", methods=["POST"])
def night_engine():
    data   = request.get_json()
    secret = data.get("secret", "")
    if secret != os.environ.get("NIGHT_SECRET", "capta2026"):
        return jsonify({"error": "Non autorisé"}), 403

    engine_state = get_engine_state()
    if not engine_state.get("active", True):
        return jsonify({"skipped": True, "reason": "Circuit breaker actif"})

    log   = load_json(DAY_LOG, {"date": "", "validations": []})
    today = datetime.now().strftime("%Y-%m-%d")
    if log.get("date") != today or not log.get("validations"):
        return jsonify({"skipped": True, "reason": "Aucune activité aujourd'hui"})

    # Calcul local uniquement — 0 token GPT
    memory  = load_json(MEMORY_FILE, {})
    updated = []
    artistes_du_jour = list(set(v["artiste"] for v in log.get("validations", [])))

    for artiste in artistes_du_jour[:10]:
        key = artist_key(artiste)
        if key not in memory:
            continue
        mem = memory[key]
        validations = mem.get("validations", 0)
        scores = mem.get("source_scores", {})
        # Normalise les scores vers 0.5 si pas utilisé depuis longtemps
        for source in scores:
            scores[source] = round(scores[source] * 0.98 + 0.5 * 0.02, 3)
        memory[key]["source_scores"] = scores
        # Score de confiance global
        if validations > 0:
            memory[key]["confidence"] = min(1.0, 0.5 + validations * 0.05)
        updated.append(artiste)

    save_json(MEMORY_FILE, memory)
    engine_success(engine_state)

    return jsonify({"run": True, "artistes_updated": updated, "total": len(updated), "tokens_used": 0})


# ── État du système ────────────────────────────────────────────────────────
@app.route("/api/status", methods=["GET"])
def status():
    engine  = get_engine_state()
    memory  = load_json(MEMORY_FILE, {})
    log     = load_json(DAY_LOG, {})
    today   = datetime.now().strftime("%Y-%m-%d")
    return jsonify({
        "engine": {
            "active":       engine.get("active", True),
            "score":        round(engine.get("score", 0.8), 2),
            "errors":       engine.get("errors", 0),
            "paused_until": engine.get("paused_until"),
            "last_run":     engine.get("last_run")
        },
        "memory": {
            "artistes_memorises": len(memory),
            "noms": list(memory.keys())[:10]
        },
        "today": {
            "validations": len(log.get("validations", [])) if log.get("date") == today else 0
        },
        "architecture": "v5"
    })


# ── Mémoire tous artistes ──────────────────────────────────────────────────
@app.route("/api/memory/all", methods=["GET"])
def all_memory():
    memory = load_json(MEMORY_FILE, {})
    result = []
    for key, data in memory.items():
        scores  = data.get("source_scores", {})
        top_src = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result.append({
            "key":           key,
            "nom":           key.replace("_", " ").title(),
            "site":          data.get("site", ""),
            "instagram":     data.get("instagram", ""),
            "validations":   data.get("validations", 0),
            "echecs":        data.get("echecs", 0),
            "confidence":    round(data.get("confidence", 0.5), 2),
            "top_sources":   [s for s, _ in top_src[:3]],
            "ev_confirmes":  len(data.get("evenements_confirmes", [])),
            "updated_at":    data.get("updated_at", ""),
        })
    result.sort(key=lambda x: x["validations"], reverse=True)
    return jsonify({"artistes": result, "total": len(result)})


# ── Mémoire artiste individuel ─────────────────────────────────────────────
@app.route("/api/memory/<artiste_name>", methods=["GET"])
def get_memory(artiste_name):
    return jsonify(get_artist_memory(artiste_name))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
