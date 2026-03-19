╔══════════════════════════════════════════════════════════╗
║       NicoDT_fast — Proxy Backend (Render)               ║
╚══════════════════════════════════════════════════════════╝

Ce dossier contient le backend proxy à déployer sur Render.com
Il permet à l'interface web d'appeler les APIs Claude, Gemini
et OpenAI sans blocage CORS.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ÉTAPE 1 — Créer un compte GitHub (si pas déjà fait)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Va sur https://github.com et crée un compte gratuit.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ÉTAPE 2 — Créer un repo GitHub avec ces fichiers
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Clique "New repository" sur GitHub
2. Nom : nicodt-fast-proxy
3. Visibility : Public (obligatoire pour Render gratuit)
4. Clique "Create repository"
5. Upload ces 4 fichiers dans le repo :
   - app.py
   - requirements.txt
   - render.yaml
   - Procfile

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ÉTAPE 3 — Déployer sur Render
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Va sur https://render.com
2. Crée un compte gratuit (avec ton GitHub)
3. Clique "New +" → "Web Service"
4. Connecte ton repo GitHub "nicodt-fast-proxy"
5. Render détecte automatiquement les paramètres via render.yaml
6. Clique "Create Web Service"
7. Attends 2-3 min que le build se termine

Tu obtiens une URL du type :
   https://nicodt-fast-proxy.onrender.com

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ÉTAPE 4 — Connecter l'interface web
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Dans l'Artifact NicoDT_fast (Claude.ai) :
1. Colle ton URL Render dans le champ "URL Proxy"
2. Saisis tes clés API dans les champs
3. Lance une recherche !

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ENDPOINTS DISPONIBLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GET  /health                → vérifie que le serveur tourne
POST /api/claude/search     → recherche web via Claude Haiku
POST /api/claude/fiche      → génère fiche captation
POST /api/gemini/fusion     → fusion et scoring Gemini
POST /api/gpt/optimize      → note d'optimisation GPT-4o

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 NOTE IMPORTANTE — Plan gratuit Render
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Le plan gratuit met le serveur en veille après 15 min
d'inactivité. La première requête après veille prend
~30 secondes (cold start). C'est normal !

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FICHIERS DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

nicodt-fast-proxy/
├── app.py            ← Serveur Flask (proxy APIs)
├── requirements.txt  ← Dépendances Python
├── render.yaml       ← Config Render auto-détectée
├── Procfile          ← Commande de démarrage
└── README.txt        ← Ce fichier
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
