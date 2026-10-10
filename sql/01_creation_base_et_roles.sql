-- =====================================================================
-- NovaScore — 01. Création des rôles et de la base de données
-- À exécuter avec le super-utilisateur PostgreSQL (postgres), sur la base « postgres ».
--
-- Exécution automatique : python installer_base.py
-- Exécution manuelle :
--   psql -U postgres -d postgres -v mdp_admin='...' -v mdp_app='...' -v mdp_lecture='...'
--        -f sql/01_creation_base_et_roles.sql
-- =====================================================================

-- Trois rôles, selon le principe du moindre privilège :
--   novascore_admin   : propriétaire de la base (installation, chargement, maintenance)
--   novascore_app     : compte utilisé par l'application Streamlit
--   novascore_lecture : compte en lecture seule (notebook, reporting)
-- Les mots de passe sont stockés par PostgreSQL sous forme chiffrée (SCRAM-SHA-256).
CREATE ROLE novascore_admin   LOGIN PASSWORD :'mdp_admin';
CREATE ROLE novascore_app     LOGIN PASSWORD :'mdp_app'     CONNECTION LIMIT 20;
CREATE ROLE novascore_lecture LOGIN PASSWORD :'mdp_lecture' CONNECTION LIMIT 10;

-- Paramètres imposés à la connexion (réservés au super-utilisateur, d'où leur place ici) :
--   le rôle de lecture ne peut ouvrir que des transactions en lecture seule ;
--   une requête de l'application est interrompue au-delà de 30 secondes.
ALTER ROLE novascore_lecture SET default_transaction_read_only = on;
ALTER ROLE novascore_app     SET statement_timeout = '30s';

COMMENT ON ROLE novascore_admin   IS 'NovaScore : propriétaire de la base (installation, chargement, maintenance)';
COMMENT ON ROLE novascore_app     IS 'NovaScore : application Streamlit (lecture et écriture sur le schéma app uniquement)';
COMMENT ON ROLE novascore_lecture IS 'NovaScore : lecture seule (notebook et reporting)';

CREATE DATABASE novascore
    OWNER novascore_admin
    ENCODING 'UTF8'
    TEMPLATE template0;

COMMENT ON DATABASE novascore IS 'NovaScore : données sources du scoring crédit et données de l''application';

-- Personne ne peut se connecter à la base sans autorisation explicite.
REVOKE ALL ON DATABASE novascore FROM PUBLIC;
GRANT CONNECT ON DATABASE novascore TO novascore_app, novascore_lecture;
