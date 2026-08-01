"""Lot B — update_session, delete_session, retire_decision.

Trois outils de maintenance du cycle de vie. Les cas decisifs :

* delete_session sur une seance LIEE au plan : la contrainte FK est en
  NO ACTION, un DELETE naif echouerait en erreur Postgres brute. L'outil doit
  refuser proprement en listant les workouts, et ne detacher que sur demande
  explicite.
* retire_decision sur une decision deja inactive : erreur, pas un no-op —
  l'appelant croit retirer du guidage actif, il vise autre chose.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastmcp.exceptions import ToolError

HIER = dt.date.today() - dt.timedelta(days=1)


# --- update_session ----------------------------------------------------------

async def test_update_session_ajoute_rpe_et_ressenti_apres_coup(client):
    """Le cas d'usage central : la seance vient d'un import Garmin (sans rpe ni
    felt), l'athlete complete son ressenti plus tard."""
    s = (
        await client.call_tool(
            "log_session",
            {"sport": "run", "date": HIER.isoformat(),
             "executed": "EF 40' importee de Garmin", "garmin_activity_id": "9101"},
        )
    ).data
    assert s["rpe"] is None

    maj = (
        await client.call_tool(
            "update_session",
            {"session_id": s["id"], "fields": {"rpe": 6, "felt": "jambes lourdes"}},
        )
    ).data
    assert maj["rpe"] == 6
    assert maj["felt"] == "jambes lourdes"
    # Les champs non passes ne bougent pas.
    assert maj["executed"] == "EF 40' importee de Garmin"
    assert maj["garmin_activity_id"] == "9101"


async def test_update_session_rejette_les_champs_proteges(client):
    s = (
        await client.call_tool("log_session", {"sport": "run", "date": HIER.isoformat()})
    ).data
    for champ, valeur in [
        ("garmin_activity_id", "42"),   # cle de dedup
        ("date", "2026-01-01"),          # identite
        ("sport", "bike"),               # identite
        ("raw", {"x": 1}),               # payload capteur
    ]:
        with pytest.raises(ToolError):
            await client.call_tool(
                "update_session", {"session_id": s["id"], "fields": {champ: valeur}}
            )


async def test_update_session_rejette_rpe_hors_bornes_et_champs_vides(client):
    s = (
        await client.call_tool("log_session", {"sport": "run", "date": HIER.isoformat()})
    ).data
    with pytest.raises(ToolError):
        await client.call_tool(
            "update_session", {"session_id": s["id"], "fields": {"rpe": 11}}
        )
    with pytest.raises(ToolError):
        await client.call_tool("update_session", {"session_id": s["id"], "fields": {}})
    with pytest.raises(ToolError):
        await client.call_tool("update_session", {"session_id": 999999,
                                                  "fields": {"rpe": 5}})


# --- delete_session ----------------------------------------------------------

async def test_delete_session_simple(client):
    s = (
        await client.call_tool(
            "log_session",
            {"sport": "run", "date": HIER.isoformat(), "garmin_activity_id": "9201"},
        )
    ).data
    res = (await client.call_tool("delete_session", {"session_id": s["id"]})).data
    assert res["deleted"] == s["id"]
    assert res["unlinked_workout_ids"] == []

    restants = (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    assert all(x["id"] != s["id"] for x in restants)


async def test_delete_session_liee_refuse_par_defaut(client):
    """Une seance liee au plan est la preuve qu'un entrainement a ete fait :
    le refus doit etre explicite et LISTER les workouts concernes."""
    w = (
        await client.call_tool(
            "plan_workout",
            {"date": HIER.isoformat(), "sport": "run", "title": "EF 40'"},
        )
    ).data
    s = (
        await client.call_tool("log_session", {"sport": "run", "date": HIER.isoformat()})
    ).data
    await client.call_tool(
        "mark_workout", {"workout_id": w["id"], "status": "done", "session_id": s["id"]}
    )

    with pytest.raises(ToolError, match=str(w["id"])):
        await client.call_tool("delete_session", {"session_id": s["id"]})

    # Rien n'a bouge : la seance existe toujours, le lien aussi.
    sessions = (await client.call_tool("get_recent_sessions", {"limit": 50})).data
    assert any(x["id"] == s["id"] for x in sessions)


async def test_delete_session_liee_detache_sur_demande(client):
    w = (
        await client.call_tool(
            "plan_workout",
            {"date": HIER.isoformat(), "sport": "run", "title": "Tempo 20'"},
        )
    ).data
    s = (
        await client.call_tool("log_session", {"sport": "run", "date": HIER.isoformat()})
    ).data
    await client.call_tool(
        "mark_workout", {"workout_id": w["id"], "status": "done", "session_id": s["id"]}
    )

    res = (
        await client.call_tool(
            "delete_session", {"session_id": s["id"], "unlink_plan": True}
        )
    ).data
    assert res["deleted"] == s["id"]
    assert res["unlinked_workout_ids"] == [w["id"]]

    # Le workout est detache mais son STATUT n'a pas ete touche : l'entrainement
    # a bien eu lieu, seule la reference a la ligne supprimee disparait.
    semaine = (await client.call_tool("get_week", {"date": HIER.isoformat()})).data
    tous = [x for jour in semaine["days"] for x in jour.get("workouts", [])]
    wk = next(x for x in tous if x["id"] == w["id"])
    assert wk["session_id"] is None
    assert wk["status"] == "done"


async def test_delete_session_inexistante(client):
    with pytest.raises(ToolError):
        await client.call_tool("delete_session", {"session_id": 999999})


# --- retire_decision ---------------------------------------------------------

async def test_retire_decision_sort_du_guidage_actif(client):
    d = (
        await client.call_tool(
            "log_coaching_decision",
            {"decision": "Verdict matin : GO", "date": HIER.isoformat()},
        )
    ).data
    retiree = (
        await client.call_tool("retire_decision", {"decision_id": d["id"]})
    ).data
    assert retiree["active"] is False

    actives = (await client.call_tool("get_active_decisions", {})).data
    assert all(x["id"] != d["id"] for x in actives)


async def test_retire_decision_deja_inactive_est_une_erreur(client):
    """Pas un no-op : si l'id vise est deja eteint, l'appelant se trompe de cible."""
    d = (
        await client.call_tool(
            "log_coaching_decision",
            {"decision": "Cadrage semaine revolue", "date": HIER.isoformat()},
        )
    ).data
    await client.call_tool("retire_decision", {"decision_id": d["id"]})
    with pytest.raises(ToolError, match="already inactive"):
        await client.call_tool("retire_decision", {"decision_id": d["id"]})


async def test_retire_decision_supersedee_est_aussi_une_erreur(client):
    """Une decision remplacee via supersedes_id est deja inactive : le retrait
    doit le dire, pas re-eteindre en silence."""
    ancienne = (
        await client.call_tool(
            "log_coaching_decision",
            {"decision": "Plan v1", "date": HIER.isoformat()},
        )
    ).data
    await client.call_tool(
        "log_coaching_decision",
        {"decision": "Plan v2", "supersedes_id": ancienne["id"],
         "date": HIER.isoformat()},
    )
    with pytest.raises(ToolError, match="already inactive"):
        await client.call_tool("retire_decision", {"decision_id": ancienne["id"]})


async def test_retire_decision_inexistante(client):
    with pytest.raises(ToolError, match="not found"):
        await client.call_tool("retire_decision", {"decision_id": 999999})
