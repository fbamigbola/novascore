-- =====================================================================
-- NovaScore — 02. Schémas, tables, contraintes et dictionnaire de données
-- À exécuter avec le rôle novascore_admin, sur la base « novascore ».
--   psql -U novascore_admin -d novascore -f sql/02_tables.sql
--
-- Trois schémas :
--   sources     : les données des fichiers CSV, typées et contrôlées (tables optimisées à l'étape 03)
--   performance : les mêmes données, sans typage ni index (tables non optimisées, pour la comparaison)
--   app         : les données de l'application NovaScore (comptes, demandes, suivi des décisions)
-- Les clés primaires et les index des tables sources sont créés à l'étape 03, après le chargement.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS sources     AUTHORIZATION novascore_admin;
CREATE SCHEMA IF NOT EXISTS performance AUTHORIZATION novascore_admin;
CREATE SCHEMA IF NOT EXISTS app         AUTHORIZATION novascore_admin;

COMMENT ON SCHEMA sources     IS 'Données sources issues des fichiers CSV (application_record.csv, credit_record.csv)';
COMMENT ON SCHEMA performance IS 'Copies non optimisées des données sources, pour mesurer l''apport des index';
COMMENT ON SCHEMA app         IS 'Données de l''application NovaScore : utilisateurs, demandes, suivi des décisions';

-- ---------------------------------------------------------------- sources.application_record
CREATE TABLE IF NOT EXISTS sources.application_record (
    ligne               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id                  BIGINT        NOT NULL,
    code_gender         CHAR(1)       NOT NULL CHECK (code_gender IN ('F', 'M')),
    flag_own_car        CHAR(1)       NOT NULL CHECK (flag_own_car IN ('Y', 'N')),
    flag_own_realty     CHAR(1)       NOT NULL CHECK (flag_own_realty IN ('Y', 'N')),
    cnt_children        SMALLINT      NOT NULL CHECK (cnt_children >= 0),
    amt_income_total    NUMERIC(12,2) NOT NULL CHECK (amt_income_total > 0),
    name_income_type    VARCHAR(40)   NOT NULL,
    name_education_type VARCHAR(40)   NOT NULL,
    name_family_status  VARCHAR(40)   NOT NULL,
    name_housing_type   VARCHAR(40)   NOT NULL,
    days_birth          INTEGER       NOT NULL CHECK (days_birth < 0),
    days_employed       INTEGER       NOT NULL,
    flag_mobil          SMALLINT      NOT NULL CHECK (flag_mobil IN (0, 1)),
    flag_work_phone     SMALLINT      NOT NULL CHECK (flag_work_phone IN (0, 1)),
    flag_phone          SMALLINT      NOT NULL CHECK (flag_phone IN (0, 1)),
    flag_email          SMALLINT      NOT NULL CHECK (flag_email IN (0, 1)),
    occupation_type     VARCHAR(40),
    cnt_fam_members     SMALLINT      NOT NULL CHECK (cnt_fam_members >= 1)
);

COMMENT ON TABLE sources.application_record IS 'Demandes de carte de crédit : profil du client au moment de la demande (une ligne par demande). Source : application_record.csv';
COMMENT ON COLUMN sources.application_record.ligne               IS 'Clé technique (ordre de la ligne dans le fichier source). L''identifiant client id n''est pas unique : 47 identifiants apparaissent deux fois';
COMMENT ON COLUMN sources.application_record.id                  IS 'Identifiant du client (lien avec credit_record.id)';
COMMENT ON COLUMN sources.application_record.code_gender         IS 'Genre du client : F (femme) ou M (homme)';
COMMENT ON COLUMN sources.application_record.flag_own_car        IS 'Possède une voiture : Y (oui) ou N (non)';
COMMENT ON COLUMN sources.application_record.flag_own_realty     IS 'Possède un bien immobilier : Y (oui) ou N (non)';
COMMENT ON COLUMN sources.application_record.cnt_children        IS 'Nombre d''enfants';
COMMENT ON COLUMN sources.application_record.amt_income_total    IS 'Revenu annuel total du client';
COMMENT ON COLUMN sources.application_record.name_income_type    IS 'Type de revenu : Working, Commercial associate, Pensioner, State servant, Student';
COMMENT ON COLUMN sources.application_record.name_education_type IS 'Niveau d''études le plus élevé';
COMMENT ON COLUMN sources.application_record.name_family_status  IS 'Situation familiale';
COMMENT ON COLUMN sources.application_record.name_housing_type   IS 'Type de logement';
COMMENT ON COLUMN sources.application_record.days_birth          IS 'Âge en jours, compté négativement à partir de la date de la demande (-12000 = environ 33 ans)';
COMMENT ON COLUMN sources.application_record.days_employed       IS 'Ancienneté dans l''emploi en jours, comptée négativement. La valeur 365243 est un code désignant les retraités sans emploi';
COMMENT ON COLUMN sources.application_record.flag_mobil          IS 'Possède un téléphone mobile (1 = oui). Variable constante : vaut 1 pour tous les clients';
COMMENT ON COLUMN sources.application_record.flag_work_phone     IS 'Possède un téléphone professionnel (1 = oui, 0 = non)';
COMMENT ON COLUMN sources.application_record.flag_phone          IS 'Possède un téléphone fixe (1 = oui, 0 = non)';
COMMENT ON COLUMN sources.application_record.flag_email          IS 'A une adresse e-mail (1 = oui, 0 = non)';
COMMENT ON COLUMN sources.application_record.occupation_type     IS 'Métier. Valeur manquante pour environ 31 % des demandes, en majorité des retraités';
COMMENT ON COLUMN sources.application_record.cnt_fam_members     IS 'Nombre de membres du foyer';

-- ---------------------------------------------------------------- sources.credit_record
CREATE TABLE IF NOT EXISTS sources.credit_record (
    id             BIGINT   NOT NULL,
    months_balance SMALLINT NOT NULL CHECK (months_balance BETWEEN -60 AND 0),
    status         CHAR(1)  NOT NULL CHECK (status IN ('0', '1', '2', '3', '4', '5', 'C', 'X'))
);

COMMENT ON TABLE sources.credit_record IS 'Historique mensuel de remboursement des clients (une ligne par client et par mois). Source : credit_record.csv';
COMMENT ON COLUMN sources.credit_record.id             IS 'Identifiant du client (lien avec application_record.id)';
COMMENT ON COLUMN sources.credit_record.months_balance IS 'Mois de l''historique, compté à rebours : 0 = mois en cours, -1 = mois précédent, jusqu''à -60';
COMMENT ON COLUMN sources.credit_record.status         IS 'Statut du mois : 0 = retard de 1 à 29 jours, 1 = 30 à 59 jours, 2 = 60 à 89 jours, 3 = 90 à 119 jours, 4 = 120 à 149 jours, 5 = plus de 150 jours ou créance passée en perte, C = mois remboursé, X = pas de crédit ce mois-là';

-- ---------------------------------------------------------------- vue : jeu de données de modélisation
-- Construit la variable cible utilisée par le notebook :
--   - ne garde que les demandes ayant un historique de crédit (sinon la cible est inconnue) ;
--   - écarte les identifiants présents plusieurs fois dans les demandes ;
--   - defaut = 1 si le client a au moins un statut 3, 4 ou 5 (retard de 90 jours ou plus).
CREATE OR REPLACE VIEW sources.v_donnees_modelisation AS
WITH ids_doublons AS (
    SELECT id FROM sources.application_record GROUP BY id HAVING COUNT(*) > 1
),
clients_avec_historique AS (
    SELECT DISTINCT id FROM sources.credit_record
),
clients_en_defaut AS (
    SELECT DISTINCT id FROM sources.credit_record WHERE status IN ('3', '4', '5')
)
SELECT a.ligne,
       a.id,
       a.code_gender, a.flag_own_car, a.flag_own_realty,
       a.cnt_children,
       a.amt_income_total::DOUBLE PRECISION AS amt_income_total,
       a.name_income_type, a.name_education_type, a.name_family_status, a.name_housing_type,
       a.days_birth, a.days_employed,
       a.flag_mobil, a.flag_work_phone, a.flag_phone, a.flag_email,
       a.occupation_type,
       a.cnt_fam_members::DOUBLE PRECISION AS cnt_fam_members,
       CASE WHEN d.id IS NULL THEN 0 ELSE 1 END AS defaut
FROM sources.application_record a
JOIN clients_avec_historique h ON h.id = a.id
LEFT JOIN clients_en_defaut d ON d.id = a.id
WHERE a.id NOT IN (SELECT id FROM ids_doublons);

COMMENT ON VIEW sources.v_donnees_modelisation IS 'Jeu de données de modélisation : demandes avec historique, sans identifiant en double, et variable cible defaut (statut 3, 4 ou 5)';

-- ---------------------------------------------------------------- performance : tables non optimisées
-- Mêmes données, mais tout en texte, sans contrainte, sans clé ni index.
CREATE TABLE IF NOT EXISTS performance.application_record_non_optimisee (
    id TEXT, code_gender TEXT, flag_own_car TEXT, flag_own_realty TEXT, cnt_children TEXT,
    amt_income_total TEXT, name_income_type TEXT, name_education_type TEXT, name_family_status TEXT,
    name_housing_type TEXT, days_birth TEXT, days_employed TEXT, flag_mobil TEXT, flag_work_phone TEXT,
    flag_phone TEXT, flag_email TEXT, occupation_type TEXT, cnt_fam_members TEXT
);
CREATE TABLE IF NOT EXISTS performance.credit_record_non_optimisee (
    id TEXT, months_balance TEXT, status TEXT
);
COMMENT ON TABLE performance.application_record_non_optimisee IS 'Copie non optimisée de sources.application_record (texte, sans clé ni index)';
COMMENT ON TABLE performance.credit_record_non_optimisee      IS 'Copie non optimisée de sources.credit_record (texte, sans clé ni index)';

-- ---------------------------------------------------------------- app : données de l'application
CREATE TABLE IF NOT EXISTS app.utilisateurs (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identifiant VARCHAR(30) NOT NULL UNIQUE CHECK (identifiant ~ '^[a-z0-9.-]{3,30}$'),
    role        VARCHAR(10) NOT NULL CHECK (role IN ('admin', 'analyste')),
    sel         CHAR(32)    NOT NULL,
    empreinte   CHAR(64)    NOT NULL,
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now(),
    cree_par    VARCHAR(30) NOT NULL,
    modifie_le  TIMESTAMPTZ
);
COMMENT ON TABLE app.utilisateurs IS 'Comptes des utilisateurs de l''application';
COMMENT ON COLUMN app.utilisateurs.identifiant IS 'Identifiant de connexion (3 à 30 caractères : minuscules, chiffres, point, tiret)';
COMMENT ON COLUMN app.utilisateurs.role        IS 'admin (accès à la gestion des utilisateurs) ou analyste';
COMMENT ON COLUMN app.utilisateurs.sel         IS 'Sel aléatoire propre à l''utilisateur (hexadécimal)';
COMMENT ON COLUMN app.utilisateurs.empreinte   IS 'Empreinte PBKDF2-SHA256 du mot de passe (200 000 itérations). Le mot de passe n''est jamais stocké en clair';

CREATE TABLE IF NOT EXISTS app.demandes (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reference      VARCHAR(12) UNIQUE,
    date           TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyste       VARCHAR(30) NOT NULL,
    donnees        JSONB       NOT NULL,
    age            NUMERIC(5,1),
    revenu         NUMERIC(12,2),
    score          NUMERIC(6,3) NOT NULL CHECK (score BETWEEN 0 AND 100),
    recommandation VARCHAR(10) NOT NULL CHECK (recommandation IN ('accord', 'refus')),
    decision       VARCHAR(10) NOT NULL CHECK (decision IN ('accord', 'refus')),
    commentaire    TEXT,
    facteurs       JSONB       NOT NULL,
    corrigee       BOOLEAN     NOT NULL DEFAULT FALSE
);
COMMENT ON TABLE app.demandes IS 'Demandes évaluées dans l''application';
COMMENT ON COLUMN app.demandes.reference      IS 'Référence lisible de la demande (DEM-000001)';
COMMENT ON COLUMN app.demandes.analyste       IS 'Identifiant de l''utilisateur qui a enregistré la demande (conservé même si le compte est supprimé)';
COMMENT ON COLUMN app.demandes.donnees        IS 'Les 17 variables transmises au modèle (JSON)';
COMMENT ON COLUMN app.demandes.score          IS 'Score de risque sur 100 (probabilité estimée par le modèle × 100)';
COMMENT ON COLUMN app.demandes.recommandation IS 'Recommandation du modèle : refus si le score dépasse le seuil de décision, accord sinon';
COMMENT ON COLUMN app.demandes.decision       IS 'Décision finale de l''analyste (peut différer de la recommandation)';
COMMENT ON COLUMN app.demandes.facteurs       IS 'Explication SHAP : contribution de chaque variable au score (JSON)';
COMMENT ON COLUMN app.demandes.corrigee       IS 'Vrai si la décision a été corrigée après l''enregistrement';

CREATE TABLE IF NOT EXISTS app.journal (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    demande_id  INTEGER     NOT NULL REFERENCES app.demandes (id),
    date        TIMESTAMPTZ NOT NULL DEFAULT now(),
    auteur      VARCHAR(30) NOT NULL,
    type        VARCHAR(12) NOT NULL CHECK (type IN ('creation', 'correction')),
    ancienne    VARCHAR(10) CHECK (ancienne IN ('accord', 'refus')),
    nouvelle    VARCHAR(10) NOT NULL CHECK (nouvelle IN ('accord', 'refus')),
    commentaire TEXT,
    CHECK (type = 'creation' OR (commentaire IS NOT NULL AND length(trim(commentaire)) > 0))
);
COMMENT ON TABLE app.journal IS 'Suivi des décisions : création et corrections. Table en ajout seul (aucune modification ni suppression autorisée à l''application)';

-- Aide à la rédaction par l'assistant IA (ajout sans perte de données sur une base existante)
ALTER TABLE app.journal ADD COLUMN IF NOT EXISTS assistance_ia BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE app.journal ADD COLUMN IF NOT EXISTS modele_ia VARCHAR(60);
COMMENT ON COLUMN app.journal.assistance_ia IS 'Vrai si le commentaire reprend une proposition rédigée par l''assistant IA, puis validée par l''analyste';
COMMENT ON COLUMN app.journal.modele_ia     IS 'Modèle de langage utilisé pour la proposition de commentaire (ex. claude-haiku-5-5), sinon vide';
COMMENT ON COLUMN app.journal.commentaire IS 'Commentaire facultatif à la création, motif obligatoire pour une correction';
