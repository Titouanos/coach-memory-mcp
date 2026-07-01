# coach-memory-mcp

Mémoire persistante long terme pour un coaching d'endurance, exposée comme
**serveur MCP distant (Streamable HTTP)**. Pensée pour **évoluer** : ajouter un
domaine métier = un module + une migration + une ligne d'enregistrement, sans
toucher au cœur.

> **Ce serveur ne fait AUCUN appel LLM.** C'est du CRUD Postgres pur : zéro clé
> API, zéro coût de tokens. Le raisonnement se fait côté claude.ai, sur votre
> abonnement. Les **données brutes** (HRV, CTL/ATL/TSB, activités) viennent d'un
> AUTRE MCP (Garmin) ; ce serveur stocke la **couche coaching** : profil,
> objectifs, courses, séances (prévu vs réalisé + ressenti), décisions et
> analyses.

---

## Sommaire

- [Architecture](#architecture)
- [Démarrage rapide (Docker)](#démarrage-rapide-docker)
- [Tests](#tests)
- [Outils MCP exposés](#outils-mcp-exposés)
- [Comment étendre (ajouter un domaine)](#comment-étendre-ajouter-un-domaine)
- [Roadmap](#roadmap)
- [Déploiement : Nginx + TLS](#déploiement--nginx--tls)
- [Sécurité : firewall + Bearer](#sécurité--firewall--bearer)
- [Ajouter le connecteur dans claude.ai](#ajouter-le-connecteur-dans-claudeai)

---

## Architecture

Séparation stricte des couches ; les *tools* sont fins, toute la logique DB vit
dans les *repositories*.

```
config.py            Settings (pydantic-settings) — env → objet typé
db.py                pool async psycopg3 + runner de migrations versionnées + ping /healthz
auth.py              middleware ASGI Bearer (constant-time), exempte /healthz
logging_setup.py     logs JSON structurés (1 objet/ligne)
models.py            modèles Pydantic v2 partagés (entrées + sorties)
repositories/        accès DB : 1 repo par domaine (+ recherche transversale dans __init__)
domains/             tools MCP : 1 module par domaine, chacun expose register(mcp, repos)
server.py            assemble tout, boucle sur DOMAIN_MODULES → register(), /healthz, sert
migrations/          NNNN_*.sql append-only, appliqués dans l'ordre au boot
```

Principes non négociables :

- **Extensibilité d'abord** — voir [Comment étendre](#comment-étendre-ajouter-un-domaine).
- **Migrations versionnées** — table `coach.schema_migrations(version, applied_at)`.
  Au boot, le runner applique dans l'ordre chaque `migrations/NNNN_*.sql` de
  version non encore enregistrée, **chacune dans sa propre transaction** (la
  DDL et la ligne du ledger commitent ensemble). Le schéma évolue par **ajout**
  de fichiers, jamais par édition rétroactive.
- **Enregistrement modulaire** — `server.py` ne connaît que `DOMAIN_MODULES` et
  appelle `register(mcp, repos)` en boucle.
- **Typé de bout en bout** — type hints + Pydantic v2 ; les contraintes (RPE
  1-10, priorité A/B/C, statuts) sont portées par le schéma des tools et
  rejetées avant d'atteindre SQL.
- **Anti-staleness** — une décision qui en *supersede* une autre bascule
  l'ancienne à `active=false` : elle reste en historique mais ne remonte plus
  dans le contexte actif.
- **Observable** — logs JSON + endpoint `/healthz` (ping DB, non authentifié).

Stack : Python 3.12, **FastMCP 3.x** (transport `streamable-http` sur `/mcp`),
**psycopg 3** async + pool, **pydantic-settings**, servi par uvicorn.

---

## Démarrage rapide (Docker)

```bash
cp .env.example .env
# éditez .env : mettez un mot de passe Postgres fort et un token :
#   openssl rand -hex 32   → MCP_AUTH_TOKEN
# et faites correspondre DATABASE_URL au couple POSTGRES_USER/PASSWORD/DB

docker compose up -d --build
```

Au démarrage, le service `mcp` attend que Postgres soit *healthy*, ouvre le pool
et applique les migrations. Vérifs :

```bash
docker compose ps
curl -s http://127.0.0.1:8000/healthz          # {"status":"ok","db":true}

# initialize MCP (le Bearer est obligatoire ; /mcp le refuse sinon → 401)
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18",
        "capabilities":{},
        "clientInfo":{"name":"curl","version":"0"}}}'
```

Logs / arrêt :

```bash
docker compose logs -f mcp
docker compose down          # -v pour supprimer aussi le volume Postgres
```

Le port n'est publié que sur `127.0.0.1:8000` : Nginx (sur l'hôte) termine le
TLS et fait office de reverse-proxy (voir plus bas).

---

## Tests

Les tests d'intégration tournent contre un vrai Postgres. Sans base configurée
(`TEST_DATABASE_URL`/`DATABASE_URL`), la suite est *skippée* (pas d'échec).

```bash
# une base jetable
docker run -d --name coach-pg \
  -e POSTGRES_USER=coach -e POSTGRES_PASSWORD=coachpw -e POSTGRES_DB=coach_memory_test \
  -p 127.0.0.1:5433:5432 postgres:16

# deps de dev + exécution
uv sync --extra dev        # ou: pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql://coach:coachpw@127.0.0.1:5433/coach_memory_test \
  uv run pytest            # ou: pytest
```

La suite couvre : l'auth (401 sans/mauvais token), `/healthz`, et **un smoke
test par tool** de bout en bout via le client in-memory de FastMCP (qui déroule
le lifespan → ouvre le pool et applique les migrations).

---

## Outils MCP exposés

| Domaine     | Tool | Rôle |
|-------------|------|------|
| context     | `get_athlete_context()` | **Le tool central.** Un appel → digest compact : profil + objectifs actifs + prochaines courses A/B + 10 dernières séances + décisions actives + 3 dernières analyses. À appeler en début de conversation. |
| profile     | `update_athlete_profile(fields)` | Upsert partiel du profil (singleton). |
| sessions    | `log_session(...)`, `get_recent_sessions(limit?)` | Journal des séances (prévu/réalisé, RPE, ressenti). |
| goals       | `set_goal(...)`, `update_goal_status(goal_id, status)`, `get_goals(status?)` | Objectifs race/perf/habit. |
| races       | `add_race(...)`, `record_race_result(race_id, actual_time, notes?)`, `get_upcoming_races()` | Calendrier de courses. |
| decisions   | `log_coaching_decision(decision, rationale?, context_snapshot?, supersedes_id?)`, `get_active_decisions()` | Décisions de coaching + anti-staleness. |
| analyses    | `record_analysis(topic, summary, key_metrics?)` | Analyses / revues. |
| search      | `search_memory(query, limit?)` | Recherche full-text Postgres sur séances / décisions / analyses. Pas de vector DB. |

---

## Comment étendre (ajouter un domaine)

Exemple : ajouter un domaine **nutrition** (repas, hydratation). Aucune
modification de `server.py`, `db.py`, `config.py`, ni des autres domaines.

**1. Une nouvelle migration** `migrations/0002_nutrition.sql` (append-only) :

```sql
CREATE TABLE coach.nutrition_logs (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date       date        NOT NULL,
    kind       text        NOT NULL,          -- meal | hydration | supplement
    detail     text        NOT NULL,
    kcal       integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX nutrition_logs_date_idx ON coach.nutrition_logs (date DESC);
```

**2. Un repository** `src/coach_memory/repositories/nutrition.py` :

```python
from __future__ import annotations
import datetime as dt
from ..models import NutritionLog          # ajoutez le modèle Pydantic dans models.py
from .base import BaseRepository

_COLS = "id, date, kind, detail, kcal, created_at"

class NutritionRepository(BaseRepository):
    async def create(self, *, date: dt.date, kind: str, detail: str, kcal: int | None = None) -> NutritionLog:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"INSERT INTO coach.nutrition_logs (date, kind, detail, kcal) "
                f"VALUES (%s,%s,%s,%s) RETURNING {_COLS}",
                (date, kind, detail, kcal),
            )
            row = await cur.fetchone()
        return NutritionLog(**row)
```

Puis exposez-le dans le conteneur `repositories/__init__.py` :

```python
from .nutrition import NutritionRepository
# dans Repositories.__init__ :
self.nutrition = NutritionRepository(pool)
```

**3. Un module domaine** `src/coach_memory/domains/nutrition.py` avec `register()` :

```python
from __future__ import annotations
import datetime as dt
from fastmcp import FastMCP
from ..repositories import Repositories

def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def log_nutrition(kind: str, detail: str, kcal: int | None = None,
                            date: dt.date | None = None) -> dict:
        """Enregistre un repas / une hydratation / un supplément."""
        row = await repos.nutrition.create(
            date=date or dt.date.today(), kind=kind, detail=detail, kcal=kcal)
        return row.model_dump(mode="json")
```

**4. Une ligne** dans `src/coach_memory/domains/__init__.py` :

```python
from . import (analyses, context, decisions, goals, nutrition, profile, races, search, sessions)
DOMAIN_MODULES = [context, profile, sessions, goals, races, decisions, analyses, nutrition, search]
```

`docker compose up -d --build` : la migration `0002` s'applique au boot, le
nouveau tool apparaît. C'est tout.

---

## Roadmap

Modules candidats — cibles que l'architecture doit **absorber sans refonte**
(non implémentés) :

- **Nutrition / liaison Feed Athlete** — repas, hydratation, poids au fil de l'eau.
- **Blocs de périodisation** — base/build/peak/taper, semaines de charge/décharge.
- **Génération & versioning de plans d'entraînement** — plans datés, diff entre versions.
- **Revue hebdomadaire automatisée** — agrège séances + métriques → `analyses`.
- **Gestion des blessures / charge** — niveaux de douleur, restrictions, retour au sport.

---

## Déploiement : Nginx + TLS

Vhost pour `coach-memory.rcmlearning.com` → `127.0.0.1:8000`. Points clés :
HTTP/1.1, `proxy_buffering off` (le Streamable HTTP utilise du SSE), en-tête
`Connection` vide.

```nginx
server {
    listen 443 ssl http2;
    server_name coach-memory.rcmlearning.com;

    ssl_certificate     /etc/letsencrypt/live/coach-memory.rcmlearning.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/coach-memory.rcmlearning.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;
        proxy_set_header Connection "";           # garde la connexion ouverte (SSE)
        proxy_buffering off;                       # ne bufferise pas le flux SSE
        proxy_cache off;
        proxy_read_timeout 3600s;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Certificat : `certbot --nginx -d coach-memory.rcmlearning.com`.
L'URL du connecteur est `https://coach-memory.rcmlearning.com/mcp`.

---

## Sécurité : firewall + Bearer

Deux couches, cumulatives :

**1. Authentification applicative — pilotée par `AUTH_ENABLED`** :

- **`AUTH_ENABLED=true` (défaut) — mode Bearer.** Le token `MCP_AUTH_TOKEN` est
  vérifié en constant-time sur **chaque** requête `/mcp` par le middleware ASGI.
  `/healthz` en est exempté. Générez le token avec `openssl rand -hex 32`, ne le
  committez jamais. Dans claude.ai, renseignez le Bearer token du connecteur.
- **`AUTH_ENABLED=false` — mode « réseau-seul ».** Le middleware n'est pas
  installé : `/mcp` passe sans vérification de token. La défense repose alors
  entièrement sur l'allowlist IP du reverse-proxy + TLS + une URL non devinable
  (point 2 ci-dessous). Dans claude.ai, ajoutez le connecteur **sans** Bearer.
  Utile quand l'allowlist IP suffit et qu'on veut éviter tout échange de secret.

Dans les deux modes, **aucun flow OAuth n'est annoncé** : le serveur ne configure
aucun *auth provider* FastMCP, donc pas d'endpoint `/.well-known/oauth-*` ni de
`401 WWW-Authenticate` qui déclencherait une négociation OAuth côté claude.ai.
Le code du middleware Bearer reste en place (simplement non câblé) : repasser en
mode authentifié = remettre `AUTH_ENABLED=true` + un token, puis rebuild.

*Évolution future : une vraie couche OAuth 2.1 (auth providers FastMCP) pourra
remplacer ce flag sans réécrire le cœur — voir [Comment étendre](#comment-étendre-ajouter-un-domaine).*

**2. Firewall restreint aux IP Anthropic** — un connecteur custom est appelé
**depuis le cloud Anthropic**, pas depuis votre navigateur. Restreignez donc le
443 aux plages IP publiées par Anthropic (doc « Anthropic IP addresses »), en
**plus** du Bearer :

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
# 443 UNIQUEMENT depuis les plages Anthropic — remplacez par la liste courante :
for cidr in <PLAGE_ANTHROPIC_1> <PLAGE_ANTHROPIC_2> ...; do
    ufw allow from "$cidr" to any port 443 proto tcp
done
ufw enable
```

> ⚠️ Les plages IP Anthropic évoluent : récupérez la **liste courante** depuis
> la documentation officielle « Anthropic IP addresses » et re-appliquez les
> règles quand elle change (idéalement via un script/cron). Ne codez pas en dur
> des IP potentiellement périmées.

Le port applicatif (8000) n'est jamais exposé directement : `docker-compose.yml`
le bind sur `127.0.0.1` uniquement, Nginx est le seul point d'entrée.

---

## Ajouter le connecteur dans claude.ai

1. **Customize** (ou Settings) → **Connectors**
2. **+** → **Add custom connector**
3. URL : `https://coach-memory.rcmlearning.com/mcp`
4. Authentification : **Bearer token** → collez la valeur de `MCP_AUTH_TOKEN`
5. Validez : les tools (`get_athlete_context`, `log_session`, …) apparaissent.

En début de conversation coaching, commencez par `get_athlete_context()`.
