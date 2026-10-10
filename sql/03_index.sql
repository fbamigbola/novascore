-- =====================================================================
-- NovaScore — 03. Optimisation : clés primaires et index
-- À exécuter avec le rôle novascore_admin, après le chargement des données.
--   psql -U novascore_admin -d novascore -f sql/03_index.sql
--
-- Les tables du schéma « sources » deviennent les tables optimisées.
-- Les tables du schéma « performance » restent sans index, pour la comparaison.
-- =====================================================================

-- Historique de crédit : un client n'a qu'une ligne par mois → clé primaire (id, months_balance).
-- Elle sert aussi d'index pour retrouver tout l'historique d'un client.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pk_credit_record') THEN
        ALTER TABLE sources.credit_record ADD CONSTRAINT pk_credit_record PRIMARY KEY (id, months_balance);
    END IF;
END $$;

-- Recherche des défauts graves (statuts 3, 4, 5) : index sur le statut, qui contient aussi l'identifiant.
CREATE INDEX IF NOT EXISTS idx_credit_record_status_id ON sources.credit_record (status, id);

-- Recherche d'une demande par identifiant client.
CREATE INDEX IF NOT EXISTS idx_application_record_id ON sources.application_record (id);

-- Application : historique trié par date, journal d'une demande.
CREATE INDEX IF NOT EXISTS idx_demandes_date      ON app.demandes (date DESC);
CREATE INDEX IF NOT EXISTS idx_journal_demande_id ON app.journal (demande_id);

-- Mise à jour des statistiques utilisées par l'optimiseur de requêtes.
ANALYZE sources.application_record;
ANALYZE sources.credit_record;
ANALYZE performance.application_record_non_optimisee;
ANALYZE performance.credit_record_non_optimisee;
