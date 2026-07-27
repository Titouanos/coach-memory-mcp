# Installation complète — Garmin + coach-memory dans Claude

Ce guide reproduit **le setup de référence de bout en bout** : deux serveurs MCP
qui travaillent ensemble dans Claude Desktop.

| Serveur | Rôle | Où il tourne | Code |
|---------|------|--------------|------|
| **garmin** | lit les **données brutes** de Garmin Connect (activités, sommeil, HRV, VO2max, séances programmées…) | en local sur votre machine, transport `stdio` | [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp) — projet tiers, pas celui-ci |
| **coach-memory** | stocke la **couche coaching** (profil, objectifs, courses, séances prévu/réalisé, décisions, analyses) | sur **votre** serveur, transport streamable-HTTP derrière TLS | ce dépôt |

Le raisonnement (analyse, planification) se fait dans Claude, sur votre
abonnement : aucun des deux serveurs n'appelle de LLM.

> ⚠️ **Une instance = un athlète.** Le profil est un *singleton* en base et
> l'authentification est un token unique partagé. Si vous donnez votre URL + votre
> token à quelqu'un d'autre, il lit et écrit **vos** données. Pour coacher
> plusieurs personnes, il faut aujourd'hui une instance (base + token + domaine)
> par athlète. Le multi-tenant n'est pas implémenté.

---

## Sommaire

- [Prérequis](#prérequis)
- [Étape 1 — le MCP Garmin](#étape-1--le-mcp-garmin)
- [Étape 2 — le serveur coach-memory](#étape-2--le-serveur-coach-memory)
- [Étape 3 — brancher les deux dans Claude Desktop](#étape-3--brancher-les-deux-dans-claude-desktop)
- [Étape 4 — première utilisation](#étape-4--première-utilisation)
- [Entretien](#entretien)
- [Dépannage](#dépannage)

---

## Prérequis

Sur votre poste :

- **Claude Desktop** (ou claude.ai pour coach-memory seul — voir la note en
  [étape 3](#étape-3--brancher-les-deux-dans-claude-desktop)).
- **[uv](https://docs.astral.sh/uv/)** pour `uvx` — lance le MCP Garmin sans
  l'installer :
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh    # → ~/.local/bin/uvx
  ```
- **Node.js** pour `npx` — nécessaire à `mcp-remote`, le pont qui permet à un
  client local de parler à un serveur MCP distant.
- Un **compte Garmin Connect** avec des données dedans.

Pour héberger coach-memory :

- une machine Linux accessible depuis Internet (un VPS à 5 €/mois suffit),
- un **nom de domaine** pointant dessus (Claude exige HTTPS pour un MCP distant),
- **Docker** + le plugin `compose`.

---

## Étape 1 — le MCP Garmin

Ce serveur n'est pas fourni ici, il s'installe directement depuis son dépôt.

### 1.1 Authentifiez-vous une fois

Ne mettez **jamais** votre mot de passe Garmin dans la configuration de Claude.
Faites plutôt un pré-login qui dépose des jetons OAuth sur le disque :

```bash
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
```

L'outil demande l'e-mail, le mot de passe et le code MFA si vous l'avez activé,
puis écrit les jetons dans `~/.garminconnect`. Ensuite le serveur fonctionne
**sans aucun identifiant** dans la config.

> Les jetons durent environ **6 mois**. Voir [Entretien](#entretien).

### 1.2 Vérifiez

```bash
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp --help
```

---

## Étape 2 — le serveur coach-memory

### 2.1 Déployez

Sur votre serveur :

```bash
git clone https://github.com/<VOTRE-COMPTE>/coach-memory-mcp.git /opt/coach-memory
cd /opt/coach-memory
cp .env.example .env
```

Éditez `.env` — deux secrets à générer, **jamais** à committer :

```bash
openssl rand -hex 32      # → MCP_AUTH_TOKEN
openssl rand -hex 24      # → POSTGRES_PASSWORD (à répercuter dans DATABASE_URL)
```

Puis :

```bash
chmod 600 .env
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz      # {"status":"ok","db":true}
```

Les migrations s'appliquent automatiquement au démarrage.

### 2.2 Exposez-le en HTTPS

Le port 8000 n'est bindé que sur `127.0.0.1` : un reverse-proxy termine le TLS.
Le [README](README.md#déploiement--nginx--tls) donne les deux vhosts Nginx
possibles (hôte dédié, ou derrière un proxy conteneurisé existant) et le détail
certbot. Les points à ne pas manquer, parce qu'ils cassent le transport SSE si on
les oublie :

```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";    # garde la connexion ouverte
proxy_buffering off;               # ne bufferise pas le flux SSE
proxy_read_timeout 3600s;
```

Vérifiez depuis l'extérieur :

```bash
curl -s https://votre-domaine.example/healthz
```

`/healthz` n'est pas authentifié, c'est normal — il ne révèle rien d'autre que
l'état de la base.

### 2.3 Vérifiez que l'auth mord

```bash
# sans token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://votre-domaine.example/mcp

# avec token → 200
curl -s -X POST https://votre-domaine.example/mcp \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18","capabilities":{},
        "clientInfo":{"name":"curl","version":"0"}}}'
```

Si le premier appel ne renvoie pas 401, arrêtez-vous là : `AUTH_ENABLED` n'est pas
à `true` et votre base est ouverte à qui connaît l'URL.

---

## Étape 3 — brancher les deux dans Claude Desktop

Éditez le fichier de configuration :

- **Linux** : `~/.config/Claude/claude_desktop_config.json`
- **macOS** : `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows** : `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python", "3.12",
        "--from", "git+https://github.com/Taxuspt/garmin_mcp",
        "garmin-mcp"
      ]
    },
    "coach-memory": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://votre-domaine.example/mcp",
        "--header",
        "Authorization: Bearer VOTRE_MCP_AUTH_TOKEN"
      ]
    }
  }
}
```

Trois pièges :

1. **Chemin de `uvx`.** Claude Desktop n'hérite pas toujours de votre `PATH`. Si
   le serveur `garmin` ne démarre pas, mettez le chemin absolu (`which uvx`, en
   général `/home/<vous>/.local/bin/uvx`).
2. **Ce fichier contient un secret en clair.** Votre `MCP_AUTH_TOKEN` y est
   lisible par tout process tournant sous votre session. Ne le partagez pas, ne
   le committez pas, et pensez-y avant de coller ce fichier dans un chat ou une
   issue.
3. **Le token est aussi votre identité.** Il n'y a pas de compte utilisateur :
   qui a le token *est* l'athlète.

Redémarrez Claude Desktop. Les deux serveurs doivent apparaître dans la liste des
connecteurs, avec leurs outils.

> **Variante claude.ai (sans Claude Desktop).** coach-memory étant un MCP
> distant, on peut aussi l'ajouter comme *connecteur custom* : Settings →
> Connectors → Add custom connector → URL `https://votre-domaine.example/mcp`,
> authentification **Bearer token**. En revanche le MCP Garmin est un process
> local (`stdio`) : il n'est pas joignable depuis le cloud Anthropic. En mode
> connecteur cloud, vous pouvez en plus restreindre le 443 aux plages IP
> d'Anthropic (voir [README](README.md#sécurité--firewall--bearer)) — mais **ne
> le faites pas** si vous utilisez Claude Desktop, cela bloquerait votre propre
> poste.

---

## Étape 4 — première utilisation

L'ordre compte : la mémoire est vide au départ, il faut l'amorcer.

1. **Amorcez le profil et les objectifs.** Dans une conversation :
   ```
   Appelle get_athlete_context, puis aide-moi à remplir mon profil
   (update_athlete_profile), mes objectifs (set_goal) et mes courses (add_race).
   ```
   Les objectifs sont **hiérarchisés** : un objectif `long_term` → un `season` →
   un `block`. Laissez délibérément à `NULL` ce que vous ne connaissez pas encore
   (FC max, seuil…) plutôt que d'inventer : ça se récupère depuis Garmin.

2. **Importez l'historique Garmin.** C'est le pont entre les deux serveurs :
   ```
   Récupère mes activités des 8 dernières semaines avec le MCP Garmin, puis
   ingère-les dans coach-memory avec ingest_garmin_activity.
   ```
   `ingest_garmin_activity` est **composite et idempotent** : il crée la séance,
   met à jour les métriques du jour, et rattache automatiquement une séance
   planifiée du même sport à ±1 jour en la passant `done`. Relancer l'import ne
   duplique rien (déduplication par `garmin_activity_id`).

3. **Commencez chaque conversation de coaching par `get_athlete_context()`.** Un
   appel renvoie un digest compact (~3 ko) : profil, objectifs actifs, prochaines
   courses A/B, 10 dernières séances, décisions actives, 3 dernières analyses,
   semaine de plan en cours et 7 jours de métriques. Sans ça, Claude repart de
   zéro à chaque fois — c'est tout l'intérêt du serveur.

4. **Faites tracer les décisions.** Quand un choix d'entraînement est arrêté,
   `log_coaching_decision(decision, rationale)`. Si une décision remplace une
   précédente, passez `supersedes_id` : l'ancienne bascule en inactif et cesse de
   polluer le contexte tout en restant dans l'historique.

---

## Entretien

| Quoi | Fréquence | Comment |
|------|-----------|---------|
| **Jetons Garmin** | ~6 mois | relancer `garmin-mcp-auth` (étape 1.1). Symptôme d'expiration : les outils Garmin renvoient une erreur d'authentification. Mettez-vous un rappel. |
| **Certificat TLS** | 90 jours | certbot renouvelle tout seul ; vérifiez que le hook de reload atteint bien votre proxy (`certbot renew --dry-run`). |
| **Sauvegarde Postgres** | à vous de voir | c'est votre historique d'entraînement, et il n'est nulle part ailleurs : `docker compose exec postgres pg_dump -U coach coach_memory > backup.sql`. |
| **Rotation du token** | si suspicion de fuite | nouveau `MCP_AUTH_TOKEN` dans `.env`, `docker compose up -d --build`, puis mise à jour de la config Claude. |
| **Mises à jour du code** | au besoin | `git pull && docker compose up -d --build` ; les migrations sont append-only et s'appliquent au boot. |

---

## Dépannage

**Le serveur `garmin` n'apparaît pas.** Chemin absolu vers `uvx` (voir piège 1),
puis regardez les logs MCP de Claude Desktop. Testez la commande à la main dans un
terminal : si elle échoue là, elle échouera dans Claude.

**`coach-memory` reste en erreur / ne se connecte pas.**
- `curl https://votre-domaine.example/healthz` depuis votre poste : si ça ne
  répond pas, le problème est DNS/TLS/firewall, pas MCP. Vérifiez notamment que
  vous n'avez pas laissé une allowlist IP Anthropic active alors que vous passez
  par Claude Desktop.
- Si `/healthz` répond mais `/mcp` non : c'est le proxy. Les quatre directives
  SSE de l'étape 2.2 sont-elles bien là ?
- Videz le cache de `mcp-remote` (`rm -rf ~/.mcp-auth`) et redémarrez.

**`mcp-remote` semble ignorer le header.** Certaines versions découpent mal un
argument contenant des espaces. Repli, avec le token dans l'environnement :

```json
{
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://votre-domaine.example/mcp",
           "--header", "Authorization:${AUTH_HEADER}"],
  "env": { "AUTH_HEADER": "Bearer VOTRE_MCP_AUTH_TOKEN" }
}
```

**401 sur `/mcp` alors que le token est bon.** Vérifiez que le proxy transmet bien
l'en-tête `Authorization` (aucun `proxy_set_header Authorization` ne doit
l'écraser) et que le token dans la config Claude est exactement celui du `.env`
du serveur — sans espace ni retour à la ligne parasite.

**Les outils répondent mais le contexte est vide.** Normal sur une base neuve :
faites l'étape 4.
