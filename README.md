# ImmoClash - Devine le prix d'un logement

Party game multijoueur temps reel:
- un host cree une room,
- les amis rejoignent avec un code,
- le serveur scrape internet au lancement de chaque partie,
- chaque manche affiche une annonce (images + zone + adresse si disponible),
- tout le monde soumet un prix,
- reveal, score, leaderboard.
- mode solo possible (1 joueur).

## Stack

- Backend: Python, FastAPI, Socket.IO
- Frontend: React, TypeScript, Vite
- Stockage: SQLite + images locales (`public/listings`)

## Arborescence

```txt
/server
  /app
  /scripts
/client
/data
/public/listings
```

## Installation

### 1) Backend

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Lancer serveur API + WebSocket

```bash
cd /home/speedy/perso_project/ImmoClash/server
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3) Frontend

```bash
cd /home/speedy/perso_project/ImmoClash/client
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Ouvre: `http://localhost:5173`

## Jouer en LAN

- Lancer backend sur `0.0.0.0:8000`
- Lancer frontend sur `0.0.0.0:5173`
- Sur les autres appareils du LAN: `http://<IP_MACHINE>:5173`
- Si necessaire, cree `client/.env` avec:

```bash
VITE_SOCKET_URL=http://<IP_MACHINE>:8000
VITE_API_URL=http://<IP_MACHINE>:8000
VITE_ASSET_BASE_URL=http://<IP_MACHINE>:8000
```

## Deploiement (Vercel + Render)

Architecture recommandee:
- Frontend React/Vite sur Vercel
- Backend FastAPI + Socket.IO sur Render
- SQLite + images sur disque persistant Render

### 1) Deployer le backend sur Render

Le repo contient `render.yaml` configure pour:
- installer `server/requirements.txt`
- lancer `uvicorn server.app.main:app`
- monter un disque persistant sur `/var/data`
- stocker DB + images dans ce volume:
  - `IMMOCLASH_DB_PATH=/var/data/immo_clash.db`
  - `IMMOCLASH_PUBLIC_DIR=/var/data/public`
- au premier demarrage si la DB persistante est vide:
  - seed automatique depuis `data/immo_clash.db` du repo
  - sinon seed automatique depuis `data/listings.json`
  - puis complementation automatique jusqu'a **30 locations minimum** (seed de secours)
  - copie automatique de `public/listings` vers le volume persistent

Etapes:
1. Sur Render, cree un nouveau service via le Blueprint du repo (fichier `render.yaml`).
2. Verifie que `GET https://<ton-backend>.onrender.com/api/health` renvoie `{"ok": true}`.

### 2) Deployer le frontend sur Vercel

Le dossier `client` contient `vercel.json` pour le fallback SPA React Router.

Etapes:
1. Importe le repo dans Vercel.
2. Regle le **Root Directory** sur `client`.
3. Variables d'environnement (Project Settings -> Environment Variables):
   - `VITE_API_URL=https://<ton-backend>.onrender.com`
   - `VITE_SOCKET_URL=https://<ton-backend>.onrender.com`
   - `VITE_ASSET_BASE_URL=https://<ton-backend>.onrender.com`
4. Lance le deploy.

### 3) (Optionnel) Durcir CORS backend

Le blueprint met `IMMOCLASH_CORS_ORIGINS=*` pour simplifier le premier deploy.
Si tu veux restreindre, remplace par tes domaines frontend autorises (separes par des virgules), par exemple:

```txt
https://immoclash.vercel.app,https://immoclash-git-main-<user>.vercel.app,http://localhost:5173
```

Puis redeploie le backend Render.

## Gameplay

- L'host configure:
  - `roundsCount`
  - `timerSeconds`
  - mode prix: `rent | sqm` (scraping uniquement location)
  - `hintsEnabled`
  - `searchQuery` (ex: `Paris, France`)
- Au `start game`, le backend scrape internet selon `searchQuery`.
- Les annonces scrapees sont enregistrees en SQLite.
- Les images web sont telechargees localement dans `public/listings/<id>/`.
- A la fin de chaque manche, **seul l'host** peut lancer la manche suivante.
- Si le pool disponible est plus petit que la config, la partie demarre avec moins de manches (degradation graceful) au lieu d'echouer.
- Quand la partie est terminee, **l'host peut la relancer** (scores remis a zero, nouveau scraping, annonces deja vues exclues).
- Le prix mensuel n'est jamais affiche pendant la manche (revele uniquement en fin de manche).
- Timer autoritaire cote serveur.
- Une seule soumission par joueur et par manche.
- Validation des inputs cote serveur.
- Reconnexion: un joueur retrouve l'etat courant de la room.

### Score

```txt
erreur% = abs(guess - truePrice) / truePrice
score = max(0, round(1000 * exp(-3 * erreur%))) - malusIndices
```

## Scraping live

- Endpoint admin: `POST /api/admin/scrape`
- Body JSON:

```json
{
  "searchQuery": "Paris, France",
  "roundsCount": 5,
  "priceMode": "rent"
}
```

- Providers implantes:
  - `pap` (principal pour FR)
  - `craigslist_rss` (fallback robuste via flux RSS)
  - `craigslist` (fallback auto)
- Fallback automatique: si un provider ne ramene pas assez d'annonces valides, le serveur passe au suivant.
- Filtrage force: **appartements en location uniquement**.
- Les champs varient selon les annonces disponibles (adresse/surface/pieces non garanties).

## Precharger 30 vraies annonces (avec images) pour la prod

Si tu veux que la prod ait deja un dataset jouable meme sans scraping live:

1. Lance un scraping seed local:

```bash
python3 server/scripts/scrape_seed.py --count 30 --batch 8 --max-runs 25
```

2. Cela met a jour:
- `data/listings.json` (30 annonces reelles),
- `public/listings/<id>/*` (images telechargees).

3. Commit/push ces fichiers dans le repo:

```bash
git add data/listings.json public/listings
git commit -m "Seed 30 real listings with images"
git push
```

4. Redeploie Render. Au startup, le backend synchronise les images du repo vers le volume persistant.

5. Optionnel: forcer la seed fallback si besoin:

```bash
curl -X POST "https://<ton-backend>.onrender.com/api/admin/seed-fallback"
```

## API HTTP

- `GET /api/health`
- `GET /api/admin/listings-count`
- `GET /api/admin/diagnostics`
- `POST /api/admin/scrape`
- `POST /api/admin/seed-fallback`

## Socket.IO events (versionnes)

Client -> Server:
- `v1:room:create`
- `v1:room:join`
- `v1:player:reconnect`
- `v1:game:start`
- `v1:round:guess:submit`
- `v1:round:hint:request`
- `v1:round:next` (host uniquement)

Server -> Client:
- `v1:room:state`
- `v1:game:started`
- `v1:round:started`
- `v1:round:result`
- `v1:game:finished`
