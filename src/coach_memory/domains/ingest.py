"""ingest domain — one-call ingestion of a Garmin activity (+ optional daily blob).

Composite tool that removes client-side orchestration: parse the activity,
create the session row, upsert the day's metrics if provided, and auto-match a
'planned' workout (same sport, ±1 day) to flip it to 'done'.

Garmin payloads vary by watch/endpoint, so extraction is deliberately tolerant:
each field is looked up under several known key spellings (including one level
of nesting via dotted paths), unknown fields are ignored, and the full daily
blob is preserved verbatim in daily_metrics.raw.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import DailyMetricsUpdate
from ..repositories import Repositories

logger = logging.getLogger("coach_memory.ingest")

_SPORT_MAP = {
    "running": "run", "trail_running": "run", "treadmill_running": "run",
    "track_running": "run", "street_running": "run",
    "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike",
    "virtual_ride": "bike", "mountain_biking": "bike", "gravel_cycling": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim", "swimming": "swim",
    "strength_training": "strength", "indoor_cardio": "cardio",
    "multi_sport": "triathlon", "triathlon": "triathlon",
}


def _pick(payload: dict, *paths: str) -> Any:
    """First non-None value among candidate keys ('a' or one-level 'a.b')."""
    for path in paths:
        node: Any = payload
        for part in path.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node is not None:
            return node
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return round(f) if f is not None else None


def _parse_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # epoch ms (Garmin beginTimestamp)
        try:
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and len(value) >= 10:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _fmt_duration(seconds: float) -> str:
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace_min_km(speed_ms: float) -> str:
    sec_per_km = 1000 / speed_ms
    m, s = divmod(round(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def _normalize_activity(activity: dict) -> dict:
    """Ramene un payload d'activite a une forme stable, quelle que soit sa source.

    DEUX CONVENTIONS DE NOMMAGE coexistent et doivent toutes deux fonctionner :

    * l'API Garmin brute (via la bibliotheque `garminconnect`), en camelCase :
      `activityId`, `startTimeLocal`, `duration`, `averageHR`...
    * le serveur MCP Garmin, qui RETRAITE ses sorties en snake_case :
      `id`, `start_time_local`, `duration_seconds`, `avg_hr_bpm`...

    Les alias snake_case ont ete ajoutes le 2026-08-01 apres constat en
    conditions reelles : sur 185 activites d'un historique de 52 semaines,
    AUCUNE n'etait importable. `id` et `start_time_local` ne figuraient dans
    aucune liste d'alias, donc chaque element echouait des la verification
    d'identifiant (« aucun identifiant Garmin : impossible de dedupliquer »).
    Le defaut etait reste invisible parce que l'ingestion quotidienne passait
    par des dictionnaires assembles a la main, jamais par la sortie du MCP.

    Le camelCase reste PRIORITAIRE dans chaque liste : quand les deux formes
    sont presentes, la source brute gagne, car elle est plus complete.
    """
    type_node = _pick(activity, "activityType", "activityTypeDTO.typeKey", "type")
    type_key = (
        type_node.get("typeKey") if isinstance(type_node, dict) else type_node
    )
    type_key = str(type_key).lower() if type_key else None

    return {
        "activity_id": _pick(activity, "activityId", "activity_id", "activityUuid", "id"),
        "name": _pick(activity, "activityName", "name", "title"),
        "date": _parse_date(
            _pick(activity, "startTimeLocal", "start_time_local", "start_time",
                  "startTimeGMT", "startTimeGmt", "start_time_gmt",
                  "beginTimestamp", "date")
        ),
        "type_key": type_key,
        "sport": _SPORT_MAP.get(type_key, type_key) if type_key else None,
        "duration_s": _as_float(_pick(activity, "duration", "movingDuration",
                                      "elapsedDuration", "duration_seconds",
                                      "moving_duration_seconds",
                                      "elapsed_duration_seconds")),
        "distance_m": _as_float(_pick(activity, "distance", "distanceInMeters",
                                      "distance_meters")),
        "avg_hr": _as_int(_pick(activity, "averageHR", "avgHr", "averageHeartRate",
                                "avgHeartRate", "avg_hr_bpm")),
        "max_hr": _as_int(_pick(activity, "maxHR", "maxHr", "maxHeartRate",
                                "max_hr_bpm")),
        "avg_speed_ms": _as_float(_pick(activity, "averageSpeed", "avgSpeed",
                                        "avg_speed_mps")),
        "load": _as_float(_pick(activity, "activityTrainingLoad", "trainingLoad",
                                "training_load")),
        "calories": _as_int(_pick(activity, "calories", "activeKilocalories")),
        "avg_power": _as_int(_pick(activity, "avgPower", "averagePower",
                                   "avg_power_watts")),
    }


def _build_summary(n: dict) -> str:
    parts: list[str] = []
    if n["name"]:
        parts.append(str(n["name"]))
    if n["duration_s"]:
        parts.append(_fmt_duration(n["duration_s"]))
    if n["distance_m"]:
        parts.append(f"{n['distance_m'] / 1000:.2f} km")
    if n["avg_speed_ms"] and n["avg_speed_ms"] > 0:
        if n["sport"] == "run":
            parts.append(f"allure {_fmt_pace_min_km(n['avg_speed_ms'])}")
        elif n["sport"] == "bike":
            parts.append(f"{n['avg_speed_ms'] * 3.6:.1f} km/h")
    if n["avg_hr"]:
        parts.append(f"FC {n['avg_hr']}/{n['max_hr']}" if n["max_hr"] else f"FC {n['avg_hr']}")
    if n["avg_power"]:
        parts.append(f"{n['avg_power']} W")
    if n["load"]:
        parts.append(f"charge {n['load']:.0f}")
    if n["calories"]:
        parts.append(f"{n['calories']} kcal")
    return ", ".join(parts)


def _gauge(value: Any, hi: int | None = None) -> int | None:
    """Gauge metrics (HRV, sleep, readiness, resting HR, body battery, VO2max):
    Garmin emits 0 as a 'no reading' sentinel — treat any non-positive value as
    missing rather than letting it fail validation downstream.

    `hi` etend la meme logique au HAUT de la plage. Au-dela du plafond accepte
    par DailyMetricsUpdate, c'est un artefact de capteur, pas une intention : on
    jette le champ comme le sentinel 0, au lieu de laisser la ValidationError
    faire echouer TOUTE l'ingestion — seance d'entrainement incluse — pour un
    seul champ de bien-etre. Le blob source reste intact dans daily_metrics.raw,
    donc la valeur brute demeure auditable et rien n'est perdu.
    """
    n = _as_int(value)
    if n is None or n <= 0:
        return None
    return None if hi is not None and n > hi else n


def _gauge_f(value: Any, hi: float | None = None) -> float | None:
    f = _as_float(value)
    if f is None or f <= 0:
        return None
    return None if hi is not None and f > hi else f


def _in_range(value: Any, lo: float, hi: float) -> float | None:
    """Nombres de charge (ctl / atl / tsb).

    Ici 0 est porteur de sens et tsb est legitimement negatif : on ne filtre donc
    PAS sur le signe, seulement sur la plage acceptee par DailyMetricsUpdate.
    Rejette aussi NaN et les infinis, qu'une simple borne basse laissait passer.
    """
    f = _as_float(value)
    if f is None or f != f:  # NaN
        return None
    return f if lo <= f <= hi else None


def _normalize_daily(daily: dict) -> dict:
    # Les plafonds ci-dessous DOIVENT rester alignes sur ceux de
    # DailyMetricsUpdate (models.py). Un champ hors plage est jete ici plutot
    # que de faire echouer l'ingestion entiere : c'est la lecon d'une regression
    # du 28/07/2026, ou une borne posee sur le modele sans filtre correspondant
    # ici faisait perdre la seance d'entrainement pour un ctl negatif envoye par
    # Garmin. Un appel EXPLICITE a upsert_daily_metrics, lui, continue de
    # rejeter une valeur aberrante : c'est une intention, pas un artefact.
    # Comme pour les activites, deux conventions coexistent : l'API brute en
    # camelCase et le serveur MCP en snake_case. Les alias snake_case ci-dessous
    # ont ete releves le 2026-08-01 sur les sorties REELLES des endpoints
    # (get_hrv_data, get_sleep_summary, get_training_readiness,
    # get_training_status), pas devinees par symetrie.
    candidates = {
        "hrv_ms": _gauge(_pick(daily, "hrv_ms", "lastNightAvg", "avgOvernightHrv",
                               "hrvSummary.lastNightAvg",
                               "last_night_avg_hrv_ms", "avg_overnight_hrv"), hi=500),
        "hrv_status": _pick(daily, "hrv_status", "hrvStatus", "hrvSummary.status"),
        "sleep_score": _gauge(_pick(daily, "sleep_score", "sleepScore",
                                    "overallSleepScore", "sleepScores.overall.value"), hi=100),
        "sleep_duration_min": _gauge(_pick(daily, "sleep_duration_min", "sleepTimeMinutes"),
                                     hi=1440),
        "resting_hr": _gauge(_pick(daily, "resting_hr", "restingHeartRate", "restingHR",
                                   "resting_heart_rate"), hi=250),
        "body_battery_start": _gauge(_pick(daily, "body_battery_start", "bodyBatteryAtWake",
                                           "body_battery_at_wake"), hi=100),
        "body_battery_end": _gauge(_pick(daily, "body_battery_end", "bodyBatteryMostRecent",
                                         "body_battery_most_recent"), hi=100),
        # "score" en dernier recours : c'est la cle du bloc renvoye par
        # get_training_readiness, dont le score de sommeil porte deja un nom
        # distinct (sleep_score) — pas de collision observee.
        "training_readiness": _gauge(_pick(daily, "training_readiness",
                                           "trainingReadiness", "trainingReadiness.score",
                                           "readiness_score", "score"),
                                     hi=100),
        # Charges : 0 compte, et tsb est legitimement negatif — on borne la
        # plage sans filtrer le signe.
        # chronic_load / acute_load sont les noms Garmin de ce que le monde de
        # l'endurance appelle CTL / ATL (get_training_status). Sans ces alias,
        # les trois colonnes restaient vides : c'etait le cas des 16 premieres
        # journees en base, toutes sans ctl ni atl ni tsb.
        "ctl": _in_range(_pick(daily, "ctl", "chronic_load", "chronicLoad"), 0, 1000),
        "atl": _in_range(_pick(daily, "atl", "acute_load", "acuteLoad"), 0, 1000),
        "tsb": _in_range(_pick(daily, "tsb"), -1000, 1000),
        "vo2max_run": _gauge_f(_pick(daily, "vo2max_run", "vo2MaxValue",
                                     "vo2_max_precise", "vo2_max"), hi=100),
        "vo2max_bike": _gauge_f(_pick(daily, "vo2max_bike", "vo2MaxCyclingValue",
                                      "cycling_vo2_max_precise", "cycling_vo2_max"), hi=100),
    }
    sleep_seconds = _gauge(_pick(daily, "sleepTimeSeconds", "sleep_seconds"), hi=1440 * 60)
    if candidates["sleep_duration_min"] is None and sleep_seconds is not None:
        candidates["sleep_duration_min"] = sleep_seconds // 60
    # tsb n'est expose par AUCUN endpoint Garmin : on le derive de sa definition
    # standard (TSB = CTL - ATL) quand les deux charges sont connues et que
    # l'appelant ne l'a pas fourni lui-meme. Une valeur explicite reste toujours
    # prioritaire. Sans cette derivation la colonne resterait vide indefiniment,
    # alors que c'est l'indicateur de fraicheur le plus lu.
    if (
        candidates["tsb"] is None
        and candidates["ctl"] is not None
        and candidates["atl"] is not None
    ):
        candidates["tsb"] = _in_range(candidates["ctl"] - candidates["atl"], -1000, 1000)
    normalized = {k: v for k, v in candidates.items() if v is not None}
    normalized["raw"] = daily
    return normalized


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def ingest_garmin_activity(
        activity: dict, daily: dict | None = None
    ) -> dict:
        """One-call ingestion of a Garmin activity (+ optional daily wellness blob).

        Does three things in one round-trip:
        1. Parses the activity JSON (tolerant to watch/endpoint variations) and
           logs a session (executed = structured summary, garmin_activity_id).
        2. If `daily` is given (sleep/HRV/readiness blob), upserts the day's
           daily_metrics (recognized fields normalized, full blob kept in raw).
        3. If a 'planned' workout of the same sport exists within ±1 day, marks
           it 'done' and links the session — the matched workout is returned so
           the coach can compare planned vs executed.

        Idempotent: re-ingesting an activity already in the log (same
        garmin_activity_id) reuses the existing session instead of duplicating
        it, then completes the remaining steps (metrics, plan match) — so a
        retry after a partial failure self-heals.

        Returns {session, session_reused, daily_metrics, matched_workout}
        (nulls where not applicable).
        """
        # ---- parse & validate EVERYTHING before writing anything ------------
        norm = _normalize_activity(activity)
        if norm["date"] is None:
            raise ToolError(
                "could not extract the activity date (looked at startTimeLocal, "
                "startTimeGMT, beginTimestamp, date)"
            )
        if not norm["sport"]:
            raise ToolError(
                "could not extract the sport (activityType.typeKey missing); "
                "pass a normalized 'activityType' in the payload"
            )

        metrics_data: dict | None = None
        if daily is not None:
            try:
                update = DailyMetricsUpdate.model_validate(_normalize_daily(daily))
            except ValidationError as exc:
                raise ToolError(f"invalid daily metrics after normalization: {exc}") from exc
            metrics_data = update.model_dump(exclude_unset=True, exclude_none=True)

        # ---- session: dedup by garmin id, else create -----------------------
        garmin_id = str(norm["activity_id"]) if norm["activity_id"] is not None else None
        session = None
        reused = False
        if garmin_id is not None:
            session = await repos.sessions.find_by_garmin_id(garmin_id)
            reused = session is not None
        if session is None:
            summary = _build_summary(norm)
            try:
                session = await repos.sessions.create(
                    date=norm["date"],
                    sport=norm["sport"],
                    type=None,
                    executed=summary or None,
                    garmin_activity_id=garmin_id,
                    # Payload conserve (migration 0007) : le resume texte perdait
                    # splits, cadence et FC par lap.
                    raw=activity,
                )
            except ValueError:
                # lost a race with a concurrent ingest of the same activity —
                # the unique index caught it; reuse the winner's row
                assert garmin_id is not None
                session = await repos.sessions.find_by_garmin_id(garmin_id)
                reused = session is not None
                if session is None:  # pragma: no cover — defensive
                    raise ToolError(
                        f"session for activity {garmin_id} exists but could not be read"
                    ) from None

        # ---- daily metrics (validated above, cannot fail on bad input) ------
        metrics_out = None
        if metrics_data is not None:
            metrics_row = await repos.metrics.upsert(norm["date"], metrics_data)
            metrics_out = metrics_row.model_dump(mode="json")

        # ---- pair prévu ↔ réalisé -------------------------------------------
        matched_out = None
        match = await repos.plan.find_planned_match(date=norm["date"], sport=norm["sport"])
        if match is not None:
            marked = await repos.plan.mark(match.id, "done", session.id)
            if marked is not None:
                matched_out = marked.model_dump(mode="json")
                logger.info(
                    "ingest matched planned workout",
                    extra={"workout_id": marked.id, "session_id": session.id},
                )

        return {
            "session": session.model_dump(mode="json"),
            "session_reused": reused,
            "daily_metrics": metrics_out,
            "matched_workout": matched_out,
        }

    # ------------------------------------------------------------------ reprise
    # Plafonds de lot. Le reverse proxy limite le corps de requete a 1 Mo sur le
    # vhost MCP — garde-fou anti-DoS pose apres audit, qu'on ne releve PAS pour
    # une commodite d'import. Une activite Garmin pese 2 a 10 Ko, d'ou ~150 par
    # appel avec de la marge. Les blobs de bien-etre pesent ~300 octets, donc
    # bien plus permissifs.
    _MAX_ACTIVITES = 150
    _MAX_DAILIES = 400
    _MAX_ECHECS_RAPPORTES = 20

    @mcp.tool
    async def backfill_garmin_history(
        activities: list[dict] | None = None,
        dailies: list[dict] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Import en masse d'un historique Garmin (activites + bien-etre).

        A UTILISER pour constituer l'historique : reprise initiale, ou rattrapage
        d'une periode manquante. Concu pour etre rejoue sans risque.

        NE PAS UTILISER pour l'activite du jour : `ingest_garmin_activity` fait
        cela mieux, car lui rapproche le realise du planifie.

        Trois differences importantes avec `ingest_garmin_activity` :

        1. AUCUN rapprochement avec le plan. Une activite historique ne doit pas
           marquer 'done' un entrainement planifie : sur un historique long, elle
           tomberait a +/-1 jour de workouts encore en attente et les cloturerait
           a tort — y compris des seances futures non encore courues.
        2. Ce qui EXISTE DEJA est IGNORE, jamais reecrit. Une activite dont
           l'identifiant Garmin est connu est sautee ; une journee de bien-etre
           deja en base est sautee. Motif : une valeur corrigee a la main a plus
           de valeur que la donnee brute de Garmin, et un import ne doit pas
           l'effacer.
        3. Un element invalide n'interrompt PAS le lot. Il est saute et rapporte,
           les autres passent.

        Une activite SANS identifiant Garmin est refusee : elle serait dupliquee
        au prochain import, faute de cle de deduplication.

        Passer `dry_run=True` pour obtenir le rapport sans rien ecrire — recommande
        avant un premier import volumineux.

        Le payload brut de chaque activite est conserve (sessions.raw), ce qui
        permet de recalculer splits, cadence et FC par lap plus tard sans
        reinterroger Garmin.
        """
        activities = activities or []
        dailies = dailies or []

        if not activities and not dailies:
            raise ToolError("rien a importer : fournis 'activities' et/ou 'dailies'")
        if len(activities) > _MAX_ACTIVITES:
            raise ToolError(
                f"{len(activities)} activites en un appel, maximum {_MAX_ACTIVITES} : "
                f"le proxy limite le corps de requete a 1 Mo. Decoupe en lots de "
                f"{_MAX_ACTIVITES} (par exemple mois par mois) et rappelle l'outil ; "
                f"il est concu pour etre rejoue sans doublon."
            )
        if len(dailies) > _MAX_DAILIES:
            raise ToolError(
                f"{len(dailies)} journees en un appel, maximum {_MAX_DAILIES}."
            )

        echecs: list[dict] = []
        dates_vues: list[dt.date] = []

        # ---- activites : on prepare TOUT avant d'ecrire quoi que ce soit -----
        preparees: list[tuple[str, dict, dict]] = []  # (garmin_id, norm, activite)
        for i, activite in enumerate(activities):
            try:
                norm = _normalize_activity(activite)
            except Exception as exc:  # payload trop deforme pour etre parse
                echecs.append({"index": i, "raison": f"parsing impossible: {exc}"})
                continue
            if norm["date"] is None:
                echecs.append({"index": i, "raison": "date introuvable"})
                continue
            if not norm["sport"]:
                echecs.append({"index": i, "raison": "sport introuvable"})
                continue
            if norm["activity_id"] is None:
                echecs.append({
                    "index": i,
                    "date": norm["date"].isoformat(),
                    "raison": "aucun identifiant Garmin : impossible de dedupliquer, "
                              "l'activite serait dupliquee au prochain import",
                })
                continue
            preparees.append((str(norm["activity_id"]), norm, activite))

        deja = await repos.sessions.existing_garmin_ids([g for g, _, _ in preparees])
        a_creer = [(g, n, a) for g, n, a in preparees if g not in deja]

        sessions_creees = 0
        if not dry_run:
            for garmin_id, norm, activite in a_creer:
                try:
                    await repos.sessions.create(
                        date=norm["date"],
                        sport=norm["sport"],
                        executed=_build_summary(norm) or None,
                        garmin_activity_id=garmin_id,
                        raw=activite,
                    )
                    sessions_creees += 1
                    dates_vues.append(norm["date"])
                except ValueError:
                    # course perdue contre un import concurrent : l'index unique a
                    # tranche, la ligne existe, c'est le resultat voulu.
                    pass
                except Exception as exc:
                    echecs.append({
                        "garmin_activity_id": garmin_id,
                        "raison": f"ecriture impossible: {exc}",
                    })
        else:
            dates_vues.extend(n["date"] for _, n, _ in a_creer)

        # ---- bien-etre : meme principe, on ignore ce qui existe --------------
        prepa_daily: list[tuple[dt.date, dict]] = []
        for i, journee in enumerate(dailies):
            brut = _normalize_daily(journee)
            date_str = _pick(journee, "date", "calendarDate", "startTimeLocal")
            jour = _parse_date(date_str) if date_str else None
            if jour is None:
                echecs.append({"index": i, "raison": "date de la journee introuvable "
                                                    "(cherche: date, calendarDate)"})
                continue
            try:
                valide = DailyMetricsUpdate.model_validate(brut)
            except ValidationError as exc:
                echecs.append({"date": jour.isoformat(),
                               "raison": f"metriques invalides: {exc.error_count()} champ(s)"})
                continue
            champs = valide.model_dump(exclude_unset=True, exclude_none=True)
            if champs:
                prepa_daily.append((jour, champs))

        # On ne SAUTE plus les journees connues, on REMPLIT LEURS TROUS.
        # `fill_holes` fait `coalesce(existant, entrant)` : une valeur corrigee a
        # la main reste intouchee, mais une colonne vide se remplit. C'etait un
        # vrai defaut de la premiere version : les 16 journees en base n'avaient
        # aucun ctl ni tsb, et « sauter ce qui existe » aurait rendu ces trous
        # definitivement inaccessibles.
        deja_renseignees = await repos.metrics.dates_with_content(
            [d for d, _ in prepa_daily]
        )
        journees_creees = 0
        journees_completees = 0
        if not dry_run:
            for jour, champs in prepa_daily:
                try:
                    _, insertion = await repos.metrics.fill_holes(jour, champs)
                    if insertion:
                        journees_creees += 1
                    else:
                        journees_completees += 1
                    dates_vues.append(jour)
                except Exception as exc:
                    echecs.append({"date": jour.isoformat(),
                                   "raison": f"ecriture impossible: {exc}"})
        else:
            dates_vues.extend(d for d, _ in prepa_daily)

        logger.info(
            "backfill garmin",
            extra={"dry_run": dry_run, "activites_recues": len(activities),
                   "sessions_creees": sessions_creees, "journees_creees": journees_creees,
                   "echecs": len(echecs)},
        )

        return {
            "dry_run": dry_run,
            "activites": {
                "recues": len(activities),
                "deja_en_base": len(deja),
                "creees" if not dry_run else "a_creer": sessions_creees if not dry_run else len(a_creer),
            },
            "journees_bien_etre": {
                "recues": len(dailies),
                "deja_renseignees": len(deja_renseignees),
                "creees": journees_creees,
                # Journees deja presentes dont des colonnes vides ont ete
                # remplies : une valeur existante n'est jamais ecrasee.
                "completees": journees_completees,
                "a_traiter" if dry_run else "_": len(prepa_daily) if dry_run else 0,
            },
            "periode_couverte": {
                "du": min(dates_vues).isoformat() if dates_vues else None,
                "au": max(dates_vues).isoformat() if dates_vues else None,
            },
            "echecs": echecs[:_MAX_ECHECS_RAPPORTES],
            "echecs_total": len(echecs),
            "plan_non_touche": True,
        }
