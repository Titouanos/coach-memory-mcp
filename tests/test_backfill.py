"""backfill_garmin_history : import en masse d'un historique Garmin.

Les deux tests decisifs sont test_backfill_ne_touche_pas_le_plan et
test_backfill_nefface_pas_une_metrique_corrigee : ils protegent contre les deux
scenarios de corruption identifies avant l'ecriture de l'outil.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastmcp.exceptions import ToolError

HIER = dt.date.today() - dt.timedelta(days=1)
VIEUX = dt.date.today() - dt.timedelta(days=300)


def activite(aid: int, jour: dt.date, sport: str = "running", **extra) -> dict:
    a = {
        "activityId": aid,
        "activityName": f"Sortie {aid}",
        "activityType": {"typeKey": sport},
        "startTimeLocal": f"{jour.isoformat()} 07:15:00",
        "duration": 2400.0,
        "distance": 8000.0,
        "averageHR": 145,
        "averageSpeed": 3.33,
    }
    a.update(extra)
    return a


async def test_backfill_ne_touche_pas_le_plan(client):
    """Une activite historique ne doit PAS cloturer un entrainement planifie.

    En production, 8 workouts etaient encore 'planned' — dont un tempo et une
    course NON ENCORE COURUS. Un import d'historique tombant a +/-1 jour les
    aurait marques 'done' a tort, via le rapprochement automatique que fait
    ingest_garmin_activity. C'est la raison d'etre d'un outil separe.
    """
    planifie = await client.call_tool(
        "plan_workout",
        {"date": HIER.isoformat(), "sport": "run", "title": "Tempo non encore couru"},
    )
    wid = planifie.data["id"]

    res = (
        await client.call_tool(
            "backfill_garmin_history", {"activities": [activite(1001, HIER)]}
        )
    ).data

    assert res["activites"]["creees"] == 1
    assert res["plan_non_touche"] is True

    # get_week rend une structure jour par jour : on aplatit pour retrouver le
    # workout par son id.
    semaine = (await client.call_tool("get_week", {"date": HIER.isoformat()})).data
    tous = [w for jour in semaine["days"] for w in jour.get("workouts", [])]
    w = next(x for x in tous if x["id"] == wid)
    assert w["status"] == "planned", "l'import a cloture un entrainement planifie"
    assert w["session_id"] is None, "l'import a lie une seance au plan"

    # controle en creux : l'ingestion UNITAIRE, elle, doit bien rapprocher
    unit = (
        await client.call_tool(
            "ingest_garmin_activity", {"activity": activite(1002, HIER)}
        )
    ).data
    assert unit["matched_workout"] is not None
    assert unit["matched_workout"]["status"] == "done"


async def test_backfill_preserve_le_manuel_et_remplit_les_trous(client):
    """Deux proprietes en un test, et elles sont indissociables.

    daily_metrics est un upsert par date. Un import naif ecraserait une valeur
    corrigee a la main par la mesure brute du capteur. Mais « sauter la journee
    si elle existe » est tout aussi faux : les 16 journees en production
    n'avaient AUCUN ctl ni tsb, et sauter les aurait rendus definitivement
    inaccessibles.

    Le regime coalesce(existant, entrant) resout les deux : l'existant gagne,
    le vide se remplit.
    """
    # journee partiellement renseignee a la main : HRV pose, sommeil absent
    await client.call_tool(
        "upsert_daily_metrics",
        {"date": HIER.isoformat(),
         "fields": {"hrv_ms": 99, "hrv_status": "CORRIGE_MAIN"}},
    )

    res = (
        await client.call_tool(
            "backfill_garmin_history",
            {"dailies": [{
                "date": HIER.isoformat(),
                "lastNightAvg": 42,          # doit etre IGNORE : hrv_ms deja pose
                "hrvStatus": "GARMIN",       # doit etre IGNORE
                "sleepScore": 86,            # doit REMPLIR le trou
                "restingHeartRate": 45,      # doit REMPLIR le trou
            }]},
        )
    ).data

    assert res["journees_bien_etre"]["deja_renseignees"] == 1
    assert res["journees_bien_etre"]["creees"] == 0
    assert res["journees_bien_etre"]["completees"] == 1

    m = (await client.call_tool("get_daily_metrics",
                                {"start_date": HIER.isoformat(),
                                 "end_date": HIER.isoformat()})).data[0]
    # ce qui etait pose a la main survit
    assert m["hrv_ms"] == 99, "une valeur corrigee a la main a ete ecrasee"
    assert m["hrv_status"] == "CORRIGE_MAIN"
    # ce qui etait vide est desormais rempli
    assert m["sleep_score"] == 86, "un trou n'a pas ete rempli"
    assert m["resting_hr"] == 45, "un trou n'a pas ete rempli"


async def test_backfill_est_idempotent(client):
    lot = [activite(2001, VIEUX), activite(2002, VIEUX + dt.timedelta(days=2))]

    p1 = (await client.call_tool("backfill_garmin_history", {"activities": lot})).data
    assert p1["activites"]["creees"] == 2
    assert p1["activites"]["deja_en_base"] == 0

    p2 = (await client.call_tool("backfill_garmin_history", {"activities": lot})).data
    assert p2["activites"]["creees"] == 0
    assert p2["activites"]["deja_en_base"] == 2

    sessions = (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    assert len([s for s in sessions if s["garmin_activity_id"] in ("2001", "2002")]) == 2


async def test_backfill_un_element_invalide_ninterrompt_pas_le_lot(client):
    lot = [
        activite(3001, VIEUX),
        {"activityId": 3002, "activityType": {"typeKey": "running"}},   # pas de date
        {"activityId": 3003, "startTimeLocal": f"{VIEUX} 08:00:00"},    # pas de sport
        activite(3004, VIEUX + dt.timedelta(days=1)),
        {"activityType": {"typeKey": "running"},                        # pas d'id Garmin
         "startTimeLocal": f"{VIEUX} 09:00:00"},
    ]
    res = (await client.call_tool("backfill_garmin_history", {"activities": lot})).data

    assert res["activites"]["creees"] == 2, "les elements valides doivent passer"
    assert res["echecs_total"] == 3
    raisons = " ".join(e["raison"] for e in res["echecs"])
    assert "date introuvable" in raisons
    assert "sport introuvable" in raisons
    assert "identifiant Garmin" in raisons


async def test_backfill_dry_run_necrit_rien(client):
    res = (
        await client.call_tool(
            "backfill_garmin_history",
            {"activities": [activite(4001, VIEUX)], "dry_run": True},
        )
    ).data
    assert res["dry_run"] is True
    assert res["activites"]["a_creer"] == 1

    sessions = (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    assert not [s for s in sessions if s["garmin_activity_id"] == "4001"]


async def test_backfill_conserve_le_payload_brut(client):
    """sessions.raw permet de recalculer splits et cadence sans rappeler Garmin."""
    a = activite(5001, VIEUX, laps=[{"lapIndex": 1, "averageHR": 160, "distance": 1000}])
    await client.call_tool("backfill_garmin_history", {"activities": [a]})

    sessions = (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    s = next(x for x in sessions if x["garmin_activity_id"] == "5001")
    assert s["raw"] is not None, "le payload brut n'a pas ete conserve"
    assert s["raw"]["laps"][0]["averageHR"] == 160


async def test_backfill_borne_la_taille_du_lot(client):
    trop = [activite(6000 + i, VIEUX) for i in range(151)]
    with pytest.raises(ToolError) as exc:
        await client.call_tool("backfill_garmin_history", {"activities": trop})
    msg = str(exc.value)
    assert "150" in msg and "1 Mo" in msg, "le message doit expliquer comment decouper"


async def test_backfill_refuse_un_appel_vide(client):
    with pytest.raises(ToolError):
        await client.call_tool("backfill_garmin_history", {})
