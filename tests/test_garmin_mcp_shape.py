"""Compatibilite avec la forme snake_case du serveur MCP Garmin.

POURQUOI CE FICHIER EXISTE. Le 2026-08-01, un import d'historique de 52 semaines
a ete prepare : 185 activites, aucune importable. Les normaliseurs n'acceptaient
que le camelCase de l'API Garmin brute (`activityId`, `startTimeLocal`), alors
que le serveur MCP RETRAITE ses sorties en snake_case (`id`, `start_time_local`).
Chaque activite echouait des la verification d'identifiant, avec le message
« aucun identifiant Garmin : impossible de dedupliquer ».

Le defaut etait invisible parce que l'ingestion quotidienne passait par des
dictionnaires assembles a la main dans la session de coaching, jamais par la
sortie brute du MCP. Personne n'avait donc confronte les deux.

Les charges utiles ci-dessous sont des captures REELLES des endpoints, relevees
ce jour-la — pas des maquettes ecrites de memoire. C'est la seule facon de
garantir que ces tests echouent si la forme amont change a nouveau.
"""

from __future__ import annotations

import datetime as dt

J = dt.date.today() - dt.timedelta(days=200)
J2 = J + dt.timedelta(days=1)
J3 = J + dt.timedelta(days=2)


# Capture de get_activity(23748518553) — activite unitaire detaillee.
def activite_mcp(jour: dt.date, aid: int = 23748518553) -> dict:
    return {
        "id": aid,
        "name": "Le Poire-sur-Vie - Lun 27/07 - EF 40' easy + 5x20\"",
        "type": "running",
        "event_type": "uncategorized",
        "start_time_local": f"{jour.isoformat()}T12:12:40.0",
        "start_time_gmt": f"{jour.isoformat()}T10:12:40.0",
        "duration_seconds": 2656.179,
        "distance_meters": 8056.26,
        "avg_speed_mps": 3.0329999923706055,
        "avg_hr_bpm": 147.0,
        "max_hr_bpm": 167.0,
        "calories": 529.0,
        "avg_cadence": 156.734375,
        "training_load": 88.38368225097656,
        "lap_count": 11,
        "has_splits": True,
    }


# Capture de get_activities_by_date — forme de LISTE, dont la cle de date differe
# de celle de l'endpoint unitaire (`start_time` et non `start_time_local`).
def activite_mcp_liste(jour: dt.date, aid: int = 23803309645) -> dict:
    return {
        "id": aid,
        "name": "Saint-Vincent-sur-Graon Running",
        "type": "running",
        "event_type": "uncategorized",
        "start_time": f"{jour.isoformat()} 17:59:54",
        "distance_meters": 10567.01953125,
        "duration_seconds": 3300.443115234375,
        "calories": 717.0,
        "avg_hr_bpm": 160.0,
        "max_hr_bpm": 181.0,
    }


async def test_activite_au_format_mcp_est_importee(client):
    """Le scenario exact qui a echoue sur les 185 activites."""
    res = (
        await client.call_tool(
            "backfill_garmin_history", {"activities": [activite_mcp(J)]}
        )
    ).data

    assert res["activites"]["creees"] == 1, (
        f"activite rejetee : {res.get('echecs')}"
    )

    s = next(
        x for x in (await client.call_tool(
            "get_recent_sessions", {"limit": 50, "include_raw": True}
        )).data
        if x["garmin_activity_id"] == "23748518553"
    )
    assert s["date"] == J.isoformat(), "date mal extraite de start_time_local"
    assert s["sport"] == "run", "type snake_case non reconnu"
    # Les champs optionnels doivent alimenter le resume, sinon ils sont perdus
    # silencieusement : c'est le mode d'echec le plus sournois, aucune erreur
    # n'est levee et la seance parait correcte.
    assert "8.06 km" in s["executed"] or "8,06" in s["executed"], s["executed"]
    assert "147" in s["executed"], f"FC moyenne absente du resume : {s['executed']}"
    # Le blob complet doit etre conserve pour les analyses ulterieures.
    assert s["raw"]["avg_cadence"] == 156.734375


async def test_activite_au_format_liste_mcp_est_importee(client):
    """get_activities_by_date nomme la date `start_time`, pas `start_time_local`.

    C'est l'endpoint qu'on utilise pour balayer un historique complet : s'il
    n'etait pas couvert, l'import de masse resterait casse meme une fois
    l'endpoint unitaire corrige.
    """
    res = (
        await client.call_tool(
            "backfill_garmin_history", {"activities": [activite_mcp_liste(J2)]}
        )
    ).data

    assert res["activites"]["creees"] == 1, f"activite rejetee : {res.get('echecs')}"
    s = next(
        x for x in (await client.call_tool("get_recent_sessions", {"limit": 50})).data
        if x["garmin_activity_id"] == "23803309645"
    )
    assert s["date"] == J2.isoformat()


async def test_le_format_brut_reste_prioritaire(client):
    """Quand les deux conventions coexistent, la source brute doit gagner.

    Elle est plus riche : si un appelant fusionne les deux, on ne veut pas que
    la version retraitee, plus pauvre, prenne le dessus.
    """
    melange = activite_mcp(J3, aid=999111)
    melange["activityId"] = 888222          # camelCase : doit gagner
    melange["startTimeLocal"] = f"{J3.isoformat()} 06:00:00"

    res = (
        await client.call_tool("backfill_garmin_history", {"activities": [melange]})
    ).data
    assert res["activites"]["creees"] == 1

    ids = [
        x["garmin_activity_id"]
        for x in (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    ]
    assert "888222" in ids, "l'identifiant camelCase aurait du primer"
    assert "999111" not in ids


async def test_bien_etre_au_format_mcp(client):
    """Fusion realiste de plusieurs endpoints bien-etre, tels qu'ils repondent.

    Une session de coaching agrege get_hrv_data + get_sleep_summary +
    get_training_readiness + get_training_status en un seul dictionnaire par
    journee. Aucune de ces sorties n'emploie les noms attendus a l'origine.
    """
    jour = J + dt.timedelta(days=10)
    fusion = {
        "date": jour.isoformat(),
        # get_hrv_data
        "last_night_avg_hrv_ms": 67,
        # get_sleep_summary
        "sleep_seconds": 32100,
        "sleep_score": 87,
        "avg_overnight_hrv": 67.0,
        # get_training_readiness
        "score": 22,
        # get_training_status
        "acute_load": 425,
        "chronic_load": 289,
        "vo2_max_precise": 58.4,
        "cycling_vo2_max_precise": 65.2,
    }

    res = (
        await client.call_tool("backfill_garmin_history", {"dailies": [fusion]})
    ).data
    assert res["journees_bien_etre"]["creees"] == 1

    m = (
        await client.call_tool(
            "get_daily_metrics",
            {"start_date": jour.isoformat(), "end_date": jour.isoformat()},
        )
    ).data[0]

    assert m["hrv_ms"] == 67
    assert m["sleep_score"] == 87
    assert m["sleep_duration_min"] == 535, "32100 s = 535 min"
    assert m["training_readiness"] == 22
    assert m["ctl"] == 289, "chronic_load non reconnu comme ctl"
    assert m["atl"] == 425, "acute_load non reconnu comme atl"
    assert m["vo2max_run"] == 58.4
    assert m["vo2max_bike"] == 65.2


async def test_tsb_est_derive_de_ctl_moins_atl(client):
    """Garmin n'expose aucun equivalent de tsb : il doit etre calcule.

    Sans cette derivation la colonne reste vide indefiniment — c'etait le cas
    des 16 premieres journees en production, toutes sans ctl, atl ni tsb.
    """
    jour = J + dt.timedelta(days=11)
    await client.call_tool(
        "backfill_garmin_history",
        {"dailies": [{"date": jour.isoformat(),
                      "chronic_load": 289, "acute_load": 425}]},
    )
    m = (
        await client.call_tool(
            "get_daily_metrics",
            {"start_date": jour.isoformat(), "end_date": jour.isoformat()},
        )
    ).data[0]
    assert m["tsb"] == -136, "tsb doit valoir ctl - atl, et peut etre negatif"


async def test_un_tsb_explicite_prime_sur_la_derivation(client):
    jour = J + dt.timedelta(days=12)
    await client.call_tool(
        "backfill_garmin_history",
        {"dailies": [{"date": jour.isoformat(), "chronic_load": 289,
                      "acute_load": 425, "tsb": -10}]},
    )
    m = (
        await client.call_tool(
            "get_daily_metrics",
            {"start_date": jour.isoformat(), "end_date": jour.isoformat()},
        )
    ).data[0]
    assert m["tsb"] == -10, "une valeur explicite a ete ecrasee par le calcul"


async def test_le_digest_nembarque_pas_les_blobs_bruts(client):
    """Consequence directe de l'import : le digest doit rester un digest.

    Le 2026-08-01, apres l'import des 52 semaines, `raw` est devenu non nul sur
    la totalite des seances. get_athlete_context embarquant les 10 dernieres,
    le digest est passe a 101 000 caracteres — dont 56 % de blobs — et a
    DEPASSE LA LIMITE DE JETONS. C'est le tout premier outil qu'appelle une
    session de coaching : le rendre irrecuperable equivaut a casser l'outil.
    """
    jour = dt.date.today() - dt.timedelta(days=1)
    gros = activite_mcp(jour, aid=555444)
    # Un blob realiste : ~5 Ko, comme une activite Garmin complete.
    gros["splits"] = [{"lap": i, "hr": 150 + i, "pace": 300 + i} for i in range(120)]
    await client.call_tool("backfill_garmin_history", {"activities": [gros]})

    ctx = (await client.call_tool("get_athlete_context", {})).data
    seances = ctx["recent_sessions"]
    assert seances, "le digest devrait contenir la seance importee"
    assert all("raw" not in s for s in seances), (
        "le digest embarque les blobs bruts : il grossira sans limite"
    )
    # Le resume texte, lui, doit rester present : c'est ce qui porte le sens.
    assert any(s.get("executed") for s in seances)


async def test_les_blobs_restent_accessibles_a_la_demande(client):
    """Exclure par defaut ne doit pas rendre la donnee inatteignable."""
    jour = dt.date.today() - dt.timedelta(days=2)
    await client.call_tool(
        "backfill_garmin_history", {"activities": [activite_mcp(jour, aid=555555)]}
    )

    sans = (await client.call_tool("get_recent_sessions", {"limit": 5})).data
    assert all("raw" not in s for s in sans), "raw doit etre exclu par defaut"

    avec = (
        await client.call_tool(
            "get_recent_sessions", {"limit": 5, "include_raw": True}
        )
    ).data
    cible = next(s for s in avec if s["garmin_activity_id"] == "555555")
    assert cible["raw"]["avg_cadence"] == 156.734375, (
        "include_raw=True doit rendre le blob complet"
    )


async def test_ingestion_unitaire_accepte_aussi_le_format_mcp(client):
    """L'autre porte d'entree doit accepter la meme forme.

    ingest_garmin_activity est le chemin quotidien : le corriger uniquement
    cote backfill laisserait la moitie du probleme en place.
    """
    jour = dt.date.today()
    res = (
        await client.call_tool(
            "ingest_garmin_activity",
            {"activity": activite_mcp(jour, aid=777333)},
        )
    ).data
    assert res["session"]["garmin_activity_id"] == "777333"
    assert res["session"]["date"] == jour.isoformat()
