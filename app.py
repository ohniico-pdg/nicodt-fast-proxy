from flask import Flask, request, jsonify
import urllib.request
import urllib.error
import json
import os
import re
import unicodedata
import concurrent.futures
from datetime import datetime, timedelta

# ── Supabase client léger (sans SDK — juste HTTP) ─────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def supa_get(table, filters=""):
    """Lecture Supabase via REST API."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filters}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except:
        return None

def supa_upsert(table, data):
    """Ecriture/mise à jour Supabase via REST API."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return True
    except:
        return False

def supa_delete(table, filters):
    """Suppression Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filters}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return True
    except:
        return False

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
    return variants[:8]

def guess_site_variants(name):
    """Génère les variantes de site officiel pour un artiste."""
    clean = normalize_name(name)
    parts = clean.split()
    if not parts:
        return []
    joined = "".join(parts)
    hyphen = "-".join(parts)
    variants = []
    for tld in [".fr", ".com"]:
        for form in [hyphen, joined]:
            variants.append(f"https://www.{form}{tld}")
            variants.append(f"https://{form}{tld}")
    return variants[:6]

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

# ── Memory helpers — Supabase avec fallback JSON local ─────────────────────
def get_artist_memory(artiste):
    key = artist_key(artiste)
    # Essaie Supabase d'abord
    rows = supa_get("memory", f"artist_key=eq.{key}&select=data")
    if rows and len(rows) > 0:
        return rows[0].get("data", {})
    # Fallback JSON local
    return load_json(MEMORY_FILE, {}).get(key, {})

def save_artist_memory(artiste, data):
    key = artist_key(artiste)
    # Charge la mémoire existante et merge
    current = get_artist_memory(artiste)
    current.update(data)
    current["updated_at"] = datetime.now().isoformat()
    # Sauvegarde Supabase
    supa_upsert("memory", {
        "artist_key": key,
        "artist_name": artiste,
        "data": current,
        "updated_at": current["updated_at"]
    })
    # Fallback JSON local aussi
    memory = load_json(MEMORY_FILE, {})
    memory[key] = current
    save_json(MEMORY_FILE, memory)

# ── NIVEAU 1 : Chemin de recherche par artiste ────────────────────────────
def save_search_path(artiste, phase_productive, details={}):
    """Mémorise quelle phase a été productive pour CET artiste."""
    mem = get_artist_memory(artiste)
    paths = mem.get("search_paths", {})
    paths[phase_productive] = {
        "productive": True,
        "details": details,
        "last_success": datetime.now().isoformat(),
        "count": paths.get(phase_productive, {}).get("count", 0) + 1
    }
    save_artist_memory(artiste, {"search_paths": paths})

def get_best_path(artiste):
    """Retourne la meilleure phase de départ pour cet artiste."""
    mem = get_artist_memory(artiste)
    paths = mem.get("search_paths", {})
    if not paths:
        return None
    # Trie par count décroissant → la phase la plus souvent productive
    best = sorted(paths.items(), key=lambda x: x[1].get("count", 0), reverse=True)
    return best[0][0] if best else None

# ── NIVEAU 3 : Consolidation à 5 validations ──────────────────────────────
def consolidate_memory_if_needed(artiste):
    """Consolidation locale après 5 validations — 0 token, en arrière-plan."""
    mem = get_artist_memory(artiste)
    validations = mem.get("validations", 0)
    last_consolidation = mem.get("last_consolidation", 0)

    # Déclenche seulement si multiple de 5 et pas déjà fait ce cycle
    if validations > 0 and validations % 5 == 0 and validations != last_consolidation:
        ev_confirmes = mem.get("evenements_confirmes", [])
        scores = mem.get("source_scores", {})

        # Calcule la source billetterie dominante
        best_source = max(scores.items(), key=lambda x: x[1])[0] if scores else ""

        # Renforce le chemin Phase 1 si site officiel connu
        paths = mem.get("search_paths", {})
        if mem.get("site") and "phase1" in paths:
            paths["phase1"]["count"] = paths["phase1"].get("count", 0) + 2  # bonus confiance

        # Déduplique les événements confirmés par date+salle
        seen = set()
        clean_events = []
        for ev in ev_confirmes:
            k = f"{ev.get('date','')}|{ev.get('salle','')}"
            if k not in seen:
                seen.add(k)
                clean_events.append(ev)

        update = {
            "last_consolidation": validations,
            "best_source": best_source,
            "search_paths": paths,
            "evenements_confirmes": clean_events,
            "confidence": min(1.0, 0.5 + validations * 0.05)
        }
        save_artist_memory(artiste, update)
        print(f"Consolidation artiste {artiste} à {validations} validations", flush=True)

# ── Cache 24h — Supabase avec fallback JSON local ──────────────────────────
def get_cache(artiste):
    key = artist_key(artiste)
    # Essaie Supabase
    rows = supa_get("cache", f"artist_key=eq.{key}&select=cached_at,results")
    if rows and len(rows) > 0:
        entry = rows[0]
    else:
        # Fallback JSON local
        entry = load_json(CACHE_FILE, {}).get(key)
    if not entry:
        return None
    try:
        diff = datetime.now() - datetime.fromisoformat(entry.get("cached_at", ""))
        if diff.total_seconds() < 86400:
            return entry.get("results", [])
    except:
        pass
    return None

def get_cache_raw(artiste):
    """Retourne les résultats en cache sans vérifier l'expiration."""
    key = artist_key(artiste)
    rows = supa_get("cache", f"artist_key=eq.{key}&select=results")
    if rows and len(rows) > 0:
        return rows[0].get("results", [])
    entry = load_json(CACHE_FILE, {}).get(key)
    return entry.get("results", []) if entry else []

def merge_results(old_results, new_results):
    """Fusionne anciens et nouveaux résultats — garde le maximum de dates."""
    combined = list(old_results) + list(new_results)
    return deduplicate_local(combined)

def save_cache(artiste, results):
    key = artist_key(artiste)
    cached_at = datetime.now().isoformat()
    # Sauvegarde Supabase
    supa_upsert("cache", {
        "artist_key": key,
        "cached_at": cached_at,
        "results": results
    })
    # Fallback JSON local
    cache = load_json(CACHE_FILE, {})
    cache[key] = {"cached_at": cached_at, "results": results}
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

# ── Sources billetterie ───────────────────────────────────────────────────
BILLETTERIE_A = ["ticketmaster.fr", "fnacspectacles.com", "seetickets.fr"]
BILLETTERIE_B = ["infoconcert.com", "billetreduc.com", "eventbrite.fr"]
ALL_BILLETTERIE = BILLETTERIE_A + BILLETTERIE_B

# ── Vérification croisée des résultats ───────────────────────────────────
def cross_verify_events(events):
    """Booste la fiabilité quand plusieurs sources trouvent le même événement."""
    groups = {}
    for ev in events:
        date = (ev.get("date") or "").strip().lower()
        lieu = (ev.get("lieu") or "").split(",")[0].strip().lower()
        key = f"{date}|{lieu}"
        if key not in groups:
            groups[key] = set()
        src = ev.get("source", "")
        if src.startswith("http"):
            try:
                groups[key].add(src.split("/")[2])
            except:
                pass
        groups[key].add(ev.get("_thread", ""))

    cross_verified = 0
    for ev in events:
        date = (ev.get("date") or "").strip().lower()
        lieu = (ev.get("lieu") or "").split(",")[0].strip().lower()
        key = f"{date}|{lieu}"
        sources = groups.get(key, set())
        sources.discard("")
        if len(sources) >= 2:
            ev["score_fiabilite"] = "eleve"
            ev["verification_croisee"] = True
            cross_verified += 1
    return events, cross_verified

# ─────────────────────────────────────────────────────────────────────────
# ROUTE PRINCIPALE — architecture v6 full-parallèle
# ─────────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    engine = get_engine_state()
    return jsonify({
        "status": "ok",
        "service": "Capta Vidéo proxy",
        "engine_active": engine.get("active", True),
        "engine_score": round(engine.get("score", 0.8), 2),
        "architecture": "v6 — full parallel + cross-verify"
    })

@app.route("/api/search", methods=["POST"])
def search():
    data     = request.get_json()
    artiste  = data.get("artiste", "").strip()
    filtres  = data.get("filtres", "")
    key_gpt  = data.get("key_gpt", "") or get_key_gpt()
    ig_input = data.get("instagram", "").strip().replace("@", "")

    if not artiste:
        return jsonify({"error": "Artiste manquant"}), 400
    if not key_gpt:
        return jsonify({"error": "Clé OpenAI manquante — configurez OPENAI_API_KEY sur Render"}), 400

    # ══════════════════════════════════════════════════════════
    # PHASE 0 — Cache 24h
    # ══════════════════════════════════════════════════════════
    mem    = get_artist_memory(artiste)
    cached = get_cache(artiste)
    if cached is not None:
        return jsonify({
            "results": cached, "source": "cache",
            "sources_used": ["cache"],
            "message": "Résultats récents réutilisés — aucun appel API consommé"
        })

    context      = build_context_from_memory(mem, artiste)
    validations  = mem.get("validations", 0)

    # ══════════════════════════════════════════════════════════
    # PRÉPARATION DES 5 THREADS PARALLÈLES
    # ══════════════════════════════════════════════════════════

    # ── Instagram variants ──
    auto_variants = guess_instagram_variants(artiste)
    if ig_input:
        hint_url    = f"https://www.instagram.com/{ig_input}/"
        ig_variants = [hint_url] + [v for v in auto_variants if v != hint_url]
    elif mem.get("instagram"):
        mem_url     = mem.get("instagram")
        ig_variants = [mem_url] + [v for v in auto_variants if v != mem_url]
    else:
        ig_variants = auto_variants
    ig_url = ig_variants[0] if ig_variants else ""

    linktree_urls = guess_linktree_variants(artiste)[:4]
    linktree_str  = ", ".join(linktree_urls)

    # ── Prompt Thread 1 — Site officiel ──
    site_url = mem.get("site")
    if site_url:
        prompt_site = f"""{context}
Va sur {site_url} et cherche la page "dates", "tournée" ou "agenda".
Artiste : "{artiste}"
Extrait TOUTES les dates disponibles 2025-2026 sans restriction de période.
Résultats DÉDUPLIQUÉS triés par date. JSON uniquement : {EVENT_SCHEMA}"""
    else:
        site_variants = guess_site_variants(artiste)
        site_hints = ", ".join(site_variants[:3])
        prompt_site = f"""{context}
Recherche : "{artiste} humoriste dates spectacle tournée 2025 2026"
1. Teste ces URL possibles pour son site : {site_hints}
2. Si trouvé, va sur sa page dates/tournée
3. Sinon cherche via Google son site officiel
4. Extrait TOUTES les dates disponibles 2025-2026 sans restriction
Salle exacte, jauge, prix billets, URL source. JSON uniquement : {EVENT_SCHEMA}"""

    # ── Prompt Thread 2 — Instagram ──
    prompt_ig = f"""{context}
Va sur {ig_url}
Vérifie que ce profil appartient à "{artiste}" (humoriste français).
Si non : retourne [].
Si oui :
1. Lis la bio — suis le lien si présent (Linktree etc.)
   Essaie aussi : {linktree_str}
2. Lis les 5 derniers posts pour des annonces de dates.
Uniquement dates 2025-2026. JSON uniquement : {EVENT_SCHEMA}"""

    # ── Prompt Thread 3 — Billetteries A (3 principales) ──
    scores      = mem.get("source_scores", {})
    sorted_a    = sorted(BILLETTERIE_A, key=lambda s: scores.get(s, 0.5), reverse=True)
    sites_str_a = "\n".join(f"- {s}" for s in sorted_a)
    prompt_bill_a = f"""{context}
Recherche TOUTES les dates de spectacle/humour de "{artiste}" en 2025-2026 sur :
{sites_str_a}
Pour CHAQUE date : nom spectacle, date exacte (JJ Mois AAAA), heure, salle avec ville, jauge de la salle, prix billets, URL de la page billet.
TOUTES les dates sans restriction de période. Résultats DÉDUPLIQUÉS. JSON uniquement : {EVENT_SCHEMA}"""

    # ── Prompt Thread 4 — Billetteries B (3 secondaires) ──
    sorted_b    = sorted(BILLETTERIE_B, key=lambda s: scores.get(s, 0.5), reverse=True)
    sites_str_b = "\n".join(f"- {s}" for s in sorted_b)
    prompt_bill_b = f"""{context}
Recherche TOUTES les dates de spectacle/humour de "{artiste}" en 2025-2026 sur :
{sites_str_b}
Pour CHAQUE date : nom spectacle, date exacte (JJ Mois AAAA), heure, salle avec ville, jauge, prix, URL source.
TOUTES les dates sans restriction. DÉDUPLIQUÉS. JSON uniquement : {EVENT_SCHEMA}"""

    # ── Prompt Thread 5 — Recherche Google directe ──
    prompt_google = f"""{context}
Recherche sur Google : "{artiste} spectacle humour dates tournée 2025 2026"
Parcours les 5 premiers résultats pertinents : sites de salles, presse culturelle, agendas locaux, pages tournée.
Extrait TOUTES les dates de spectacles trouvées.
Ignore les résultats déjà couverts par ticketmaster, fnac, seetickets, infoconcert, billetreduc, eventbrite.
Uniquement dates 2025-2026. JSON uniquement : {EVENT_SCHEMA}"""

    # ══════════════════════════════════════════════════════════
    # EXÉCUTION PARALLÈLE — 5 threads simultanés
    # ══════════════════════════════════════════════════════════
    results_by_thread = {}
    working_ig = None

    def run_thread(name, prompt, timeout=45):
        try:
            raw = gpt_search(key_gpt, prompt, timeout=timeout)
            found = extract_json(raw)
            for ev in found:
                ev["_thread"] = name
            return found
        except Exception as e:
            print(f"Thread {name} error: {e}", flush=True)
            return []

    def run_site():
        found = run_thread("site_officiel", prompt_site, timeout=45)
        if found and not site_url:
            for ev in found:
                if ev.get("site_officiel"):
                    save_artist_memory(artiste, {"site": ev["site_officiel"]})
                    break
        return found

    def run_instagram():
        nonlocal working_ig
        found = run_thread("instagram", prompt_ig, timeout=40)
        if found and ig_url:
            working_ig = ig_url
        return found

    def run_bill_a():
        return run_thread("billetterie_a", prompt_bill_a, timeout=45)

    def run_bill_b():
        return run_thread("billetterie_b", prompt_bill_b, timeout=45)

    def run_google():
        return run_thread("google_direct", prompt_google, timeout=40)

    threads = [
        ("site_officiel", run_site),
        ("instagram",     run_instagram),
        ("billetterie_a", run_bill_a),
        ("billetterie_b", run_bill_b),
        ("google_direct", run_google),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fn): name for name, fn in threads}

        for future in concurrent.futures.as_completed(futures, timeout=70):
            name = futures[future]
            try:
                found = future.result(timeout=5)
                results_by_thread[name] = found
            except Exception as e:
                print(f"Future {name} error: {e}", flush=True)
                results_by_thread[name] = []

    # Sauvegarde Instagram si trouvé
    if working_ig:
        save_artist_memory(artiste, {"instagram": working_ig})

    # ══════════════════════════════════════════════════════════
    # FUSION + VÉRIFICATION CROISÉE + DÉDUPLICATION
    # ══════════════════════════════════════════════════════════
    all_events = []
    sources_used = []
    sources_detail = {}

    for name, found in results_by_thread.items():
        if found:
            all_events.extend(found)
            sources_used.append(name)
            sources_detail[name] = len(found)

    # Déduplication locale
    events = deduplicate_local(all_events)

    # Vérification croisée — booste la fiabilité des doublons inter-sources
    events, cross_count = cross_verify_events(events)

    # Nettoyage du champ interne _thread
    for ev in events:
        ev.pop("_thread", None)

    # Mise à jour des scores sources
    for name in sources_used:
        if name in ("site_officiel", "instagram"):
            update_source_scores(artiste, [name], True)
        elif "billetterie" in name:
            billetterie_list = BILLETTERIE_A if name == "billetterie_a" else BILLETTERIE_B
            update_source_scores(artiste, billetterie_list, True)

    # Aucun résultat
    if not events:
        echecs = get_artist_memory(artiste).get("echecs", 0) + 1
        save_artist_memory(artiste, {"echecs": echecs})
        suggestion = f"Vérifiez l'orthographe de '{artiste}'" if echecs >= 3 else ""
        return jsonify({
            "results": [], "sources_used": sources_used,
            "sources_detail": sources_detail,
            "echecs": echecs, "suggestion": suggestion
        })

    # Fusion avec résultats précédents en cache
    previous = get_cache_raw(artiste)
    if previous:
        events = merge_results(previous, events)

    save_cache(artiste, events)

    return jsonify({
        "results": events,
        "sources_used": sources_used,
        "sources_detail": sources_detail,
        "cross_verified": cross_count,
        "total_threads": len(results_by_thread),
        "message": f"{len(events)} dates trouvées via {len(sources_used)} source{'s' if len(sources_used)>1 else ''}"
    })


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

    # Niveau 1 — mémorise le chemin de recherche productif
    sources = data.get("sources_used", [])
    if "site_officiel" in sources:
        save_search_path(artiste, "phase1", {"site": site})
    if "instagram" in sources:
        save_search_path(artiste, "phase2", {"handle": insta})
    if any(s for s in sources if "billetterie" in s or s == "google_direct" or s == "court_circuit_billetterie"):
        save_search_path(artiste, "phase3", {"source": source})

    # Log journalier
    log = load_json(DAY_LOG, {"date": "", "validations": []})
    today = datetime.now().strftime("%Y-%m-%d")
    if log.get("date") != today:
        log = {"date": today, "validations": []}
    log["validations"].append({"artiste": artiste, "event": event.get("nom", ""), "at": datetime.now().isoformat()})
    save_json(DAY_LOG, log)

    # Niveau 3 — consolidation si multiple de 5
    consolidate_memory_if_needed(artiste)

    return jsonify({"saved": True, "validations": update["validations"]})


# ── NIVEAU 2 : Validation manuelle par champ ──────────────────────────────
@app.route("/api/confirm-lieu", methods=["POST"])
def confirm_lieu():
    """Confirme et mémorise les données d'un événement champ par champ.
    Les données manuelles ont priorité absolue sur GPT (source='manuel')."""
    data    = request.get_json()
    artiste = data.get("artiste", "").strip()
    event   = data.get("event", {})
    # Données manuelles optionnelles
    manuel = data.get("manuel", {})

    if not artiste or not event:
        return jsonify({"error": "Données manquantes"}), 400

    mem = get_artist_memory(artiste)
    ev_confirmes = mem.get("evenements_confirmes", [])

    date_ev  = event.get("date", "")
    salle_ev = (manuel.get("salle") or event.get("lieu", "")).split(",")[0].strip()

    # Cherche si événement déjà confirmé pour mise à jour
    existing_idx = None
    for idx, e in enumerate(ev_confirmes):
        if e.get("date") == date_ev and e.get("salle") == salle_ev:
            existing_idx = idx
            break

    # Construit l'entrée — données manuelles prioritaires
    entry = {
        "date":       date_ev,
        "artiste":    artiste,
        "salle":      salle_ev,
        "adresse":    manuel.get("adresse") or event.get("lieu", ""),
        "jauge":      manuel.get("jauge") or event.get("jauge", ""),
        "prix":       manuel.get("prix") or event.get("prix_billets", ""),
        "contact":    manuel.get("contact") or event.get("contact_production", ""),
        "site_lieu":  manuel.get("site_lieu", ""),
        "source":     event.get("source", ""),
        "confirme_le": datetime.now().isoformat(),
        # Marque les champs saisis manuellement
        "champs_manuels": [k for k in manuel if manuel[k]]
    }

    if existing_idx is not None:
        # Mise à jour — fusion avec priorité aux nouvelles données manuelles
        old = ev_confirmes[existing_idx]
        for k, v in entry.items():
            if v:  # écrase seulement si valeur non vide
                old[k] = v
        ev_confirmes[existing_idx] = old
    else:
        ev_confirmes.append(entry)

    save_artist_memory(artiste, {"evenements_confirmes": ev_confirmes})

    # Déclenche consolidation si nécessaire (après 5 validations)
    consolidate_memory_if_needed(artiste)

    return jsonify({
        "confirmed": True,
        "total_confirmes": len(ev_confirmes),
        "champs_manuels": entry["champs_manuels"]
    })

# ── Correction d'une donnée validée (panneau Mémoire) ─────────────────────
@app.route("/api/memory/update", methods=["POST"])
def update_memory():
    """Corrige une donnée dans la mémoire artiste — zéro impact sur recherches."""
    data    = request.get_json()
    artiste = data.get("artiste", "").strip()
    champ   = data.get("champ", "").strip()
    valeur  = data.get("valeur", "")
    date_ev = data.get("date_ev", "")  # si correction sur un événement spécifique

    if not artiste or not champ:
        return jsonify({"error": "Données manquantes"}), 400

    # Suppression complète d'un artiste
    if champ == "_delete":
        key = artist_key(artiste)
        # Supprime de Supabase
        supa_delete("memory", f"artist_key=eq.{key}")
        supa_delete("cache", f"artist_key=eq.{key}")
        # Supprime du JSON local
        memory = load_json(MEMORY_FILE, {})
        memory.pop(key, None)
        save_json(MEMORY_FILE, memory)
        cache = load_json(CACHE_FILE, {})
        cache.pop(key, None)
        save_json(CACHE_FILE, cache)
        return jsonify({"deleted": True, "artiste": artiste})

    mem = get_artist_memory(artiste)

    if date_ev:
        # Correction sur un événement spécifique
        ev_confirmes = mem.get("evenements_confirmes", [])
        for ev in ev_confirmes:
            if ev.get("date") == date_ev:
                ev[champ] = valeur
                ev["champs_manuels"] = list(set(ev.get("champs_manuels", []) + [champ]))
                ev["corrige_le"] = datetime.now().isoformat()
                break
        save_artist_memory(artiste, {"evenements_confirmes": ev_confirmes})
    else:
        # Correction sur la fiche artiste (site, instagram, etc.)
        save_artist_memory(artiste, {champ: valeur})

    return jsonify({"updated": True, "champ": champ, "artiste": artiste})


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
        "architecture": "v6"
    })


# ── Mémoire tous artistes ──────────────────────────────────────────────────
@app.route("/api/memory/all", methods=["GET"])
def all_memory():
    # Essaie Supabase d'abord
    rows = supa_get("memory", "select=artist_key,artist_name,data,updated_at&order=updated_at.desc")
    if rows is None:
        # Fallback JSON local
        memory = load_json(MEMORY_FILE, {})
        rows = [{"artist_key": k, "artist_name": k.replace("_"," ").title(), "data": v} for k, v in memory.items()]
    result = []
    for row in rows:
        data    = row.get("data", {})
        scores  = data.get("source_scores", {})
        top_src = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result.append({
            "key":          row.get("artist_key", ""),
            "nom":          row.get("artist_name", row.get("artist_key","").replace("_"," ").title()),
            "site":         data.get("site", ""),
            "instagram":    data.get("instagram", ""),
            "validations":  data.get("validations", 0),
            "echecs":       data.get("echecs", 0),
            "confidence":   round(data.get("confidence", 0.5), 2),
            "top_sources":  [s for s, _ in top_src[:3]],
            "ev_confirmes": len(data.get("evenements_confirmes", [])),
            "updated_at":   row.get("updated_at", data.get("updated_at", "")),
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
