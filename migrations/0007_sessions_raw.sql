-- 0007 — conserver le payload Garmin brut de chaque séance
--
-- Motivation. `sessions` ne gardait qu'un résumé TEXTE (`executed`), construit à
-- l'ingestion. Tout le détail du payload Garmin était donc perdu : splits par
-- lap, cadence, FC par intervalle, dénivelé, allure instantanée.
--
-- Le coût s'est révélé concret le 29/07/2026 : impossible d'analyser « les
-- splits réels de 12 séances clés » sur 52 semaines sans réinterroger Garmin —
-- un service qui ne répond que depuis Claude Desktop, dont les outils
-- disparaissent de l'index, et qui était injoignable au moment où la question
-- s'est posée. La donnée existait, elle avait juste été jetée après lecture.
--
-- `daily_metrics` conservait déjà son blob (`raw jsonb`), et cette décision
-- s'est révélée juste à plusieurs reprises : c'est ce qui a permis de garder
-- les valeurs brutes quand la normalisation rejetait un champ hors plage. On
-- applique la même règle aux séances.
--
-- Volume : ~5 Ko par activité, soit environ 1,3 Mo pour 52 semaines. Négligeable
-- au regard de la valeur analytique.
--
-- Nullable sans défaut : les 7 séances existantes gardent `raw = NULL` sans
-- réécriture, et rien ne dépend de sa présence.

ALTER TABLE coach.sessions
    ADD COLUMN raw jsonb;

COMMENT ON COLUMN coach.sessions.raw IS
    'Payload Garmin brut de l''activité, tel que reçu. Permet de recalculer '
    'splits, cadence et FC par lap sans réinterroger Garmin.';
