-- =====================================================================
-- NovaScore — 04. Sécurisation : droits par rôle et vue anonymisée
-- À exécuter avec le rôle novascore_admin, sur la base « novascore ».
--   psql -U novascore_admin -d novascore -f sql/04_securite.sql
-- =====================================================================

-- 1. Aucun droit par défaut pour les autres utilisateurs de PostgreSQL.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA sources, performance, app FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sources, performance, app FROM PUBLIC;

-- 2. Rôle de lecture (notebook, reporting) : lecture seule sur les données sources.
GRANT USAGE ON SCHEMA sources TO novascore_lecture;
GRANT SELECT ON ALL TABLES IN SCHEMA sources TO novascore_lecture;
-- (lecture seule imposée à la connexion : voir sql/01, paramètre default_transaction_read_only)

-- 3. Rôle de l'application : uniquement le schéma app, avec des droits limités par table.
--    Aucun accès aux données sources ni aux tables de performance.
GRANT USAGE ON SCHEMA app TO novascore_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.utilisateurs TO novascore_app;  -- gestion des comptes
GRANT SELECT, INSERT, UPDATE         ON app.demandes     TO novascore_app;  -- pas de suppression de demande
GRANT SELECT, INSERT                 ON app.journal      TO novascore_app;  -- ajout seul : traçabilité
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO novascore_app;
-- (durée maximale d'une requête : 30 s, voir sql/01)

-- 4. Vue anonymisée pour le reporting : pas d'âge ni de revenu exacts, pas de données saisies.
CREATE OR REPLACE VIEW app.v_demandes_anonymisees AS
SELECT reference,
       date_trunc('day', date)::DATE AS jour,
       CASE WHEN age < 30 THEN 'moins de 30 ans'
            WHEN age < 45 THEN '30 à 44 ans'
            WHEN age < 60 THEN '45 à 59 ans'
            ELSE '60 ans et plus' END AS tranche_age,
       CASE WHEN revenu < 100000 THEN 'moins de 100 000'
            WHEN revenu < 200000 THEN '100 000 à 199 999'
            ELSE '200 000 et plus' END AS tranche_revenu,
       round(score)::INTEGER AS score,
       recommandation,
       decision,
       (decision <> recommandation) AS decision_modifiee,
       corrigee
FROM app.demandes;

COMMENT ON VIEW app.v_demandes_anonymisees IS 'Demandes sans données personnelles exactes (tranches d''âge et de revenu), pour le reporting';

GRANT USAGE ON SCHEMA app TO novascore_lecture;
GRANT SELECT ON app.v_demandes_anonymisees TO novascore_lecture;
