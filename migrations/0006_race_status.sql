-- 0006 — statut de course : planned / done / cancelled
--
-- Motivation. Il n'existait aucun moyen de retirer une course du calendrier :
-- ni outil de suppression, ni colonne d'état. Quand le triathlon Sud Vendée du
-- 27/09/2026 a été annulé, la seule échappatoire a été de le neutraliser par
-- une décision de coaching — la course restait visible dans
-- get_upcoming_races, ce qui pollue toute planification automatique.
--
-- Choix de conception : un STATUT, pas une suppression. Effacer la ligne
-- perdrait la trace qu'une course avait été planifiée puis abandonnée, alors
-- que c'est précisément le genre d'information qu'une mémoire de coaching doit
-- conserver — au même titre que goals.status, qui distingue déjà 'abandoned'
-- de la disparition.
--
-- Le défaut 'planned' rend la migration transparente pour les lignes
-- existantes, et le backfill marque 'done' celles qui portent déjà un
-- résultat : sans lui, une course courue resterait éternellement « à venir ».

ALTER TABLE coach.races
    ADD COLUMN status text NOT NULL DEFAULT 'planned';

ALTER TABLE coach.races
    ADD CONSTRAINT races_status_chk
    CHECK (status IN ('planned', 'done', 'cancelled'));

-- Une course avec un temps enregistré a forcément été courue.
UPDATE coach.races
   SET status = 'done'
 WHERE actual_time IS NOT NULL;

-- get_upcoming_races filtre désormais sur le statut : l'index sert ce chemin,
-- qui est le plus fréquent de la table.
CREATE INDEX races_status_idx ON coach.races (status);
