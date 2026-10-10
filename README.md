# NovaScore — scoring crédit et aide à la décision

NovaScore aide un analyste crédit à traiter les demandes de carte de crédit. Pour chaque demande, l'application calcule un **score de risque de défaut grave** (retard de 90 jours ou plus), affiche une **recommandation** (« Accord recommandé » ou « Refus recommandé – dossier à examiner par un analyste ») et **explique** le score (SHAP). La décision finale appartient à l'analyste ; elle est enregistrée et tracée.

Le projet comprend :

- une base **PostgreSQL** alimentée à partir de deux fichiers CSV ;
- un **notebook** qui prépare les données, entraîne et compare quatre modèles, choisit le seuil de décision et explique le modèle ;
- une **application web Streamlit** pour les analystes et les administrateurs.

---

## Sommaire

1. [Accès en ligne](#1-accès-en-ligne)
2. [Identifiants](#2-identifiants)
3. [Contenu du dossier](#3-contenu-du-dossier)
4. [Prérequis](#4-prérequis)
5. [Installation locale](#5-installation-locale)
6. [Utilisation](#6-utilisation)
7. [Base de données](#7-base-de-données)
8. [Restaurer la base depuis le dump SQL](#8-restaurer-la-base-depuis-le-dump-sql)
9. [Accès administrateur (back office)](#9-accès-administrateur-back-office)
10. [Compatibilité navigateurs](#10-compatibilité-navigateurs)
11. [Sécurité et protection des données](#11-sécurité-et-protection-des-données)
12. [Dépannage](#12-dépannage)

---

## 1. Accès en ligne

| | |
|---|---|
| **URL publique de l'application** | *à compléter après le déploiement* |
| Hébergement de l'application | Streamlit Community Cloud |
| Hébergement de la base | PostgreSQL managé (*à compléter*) |

---

## 2. Identifiants

### Application (comptes de test)

| Rôle | Identifiant | Mot de passe | Accès |
|---|---|---|---|
| Administrateur | `admin` | `admin` | Tableau de bord, Nouvelle demande, Historique, **Utilisateurs** |
| Analyste | `analyste.demo` | `Analyste2026` | Tableau de bord, Nouvelle demande, Historique |

Ces comptes sont créés par `installer_base.py`. Sur la version en ligne, le mot de passe de l'administrateur est remplacé par un mot de passe robuste, communiqué séparément.

### Base de données PostgreSQL

| Rôle | Mot de passe (démonstration) | Utilisé par | Droits |
|---|---|---|---|
| `postgres` | celui choisi à l'installation de PostgreSQL | `installer_base.py`, `sauvegarder_base.py` | Super-utilisateur : création de la base et des rôles |
| `novascore_admin` | `NsAdmin_2026!` | installation, chargement, maintenance, pgAdmin | Propriétaire de la base |
| `novascore_app` | `NsApp_2026!` | l'application Streamlit | Schéma `app` uniquement : pas de suppression de demande, journal en ajout seul |
| `novascore_lecture` | `NsLecture_2026!` | le notebook, le reporting | Lecture seule : données sources et vue anonymisée des demandes |

Serveur : `localhost`, port `5432`, base `novascore`. Ces valeurs sont lues dans le fichier `.env`. Les mots de passe ci-dessus sont des **valeurs de démonstration** : à changer pour toute autre utilisation.

---

## 3. Contenu du dossier

```text
Scoring_Credit_Projet_Complet/
├── README.md                    ← ce fichier
├── application_record.csv       ← données sources : une ligne par demande (profil du client)
├── credit_record.csv            ← données sources : historique mensuel de remboursement
├── .env.exemple                 ← modèle du fichier de configuration (à copier en .env)
├── .env.en_ligne.exemple        ← modèle de configuration de la base en ligne (déploiement)
├── .gitignore                   ← fichiers exclus du dépôt (dont .env)
├── requirements.txt             ← librairies de l'application (versions fixées, déploiement)
├── requirements-notebook.txt    ← librairies du notebook et des scripts (poste local)
│
├── sql/                         ← scripts SQL, exécutés dans l'ordre par installer_base.py
│   ├── 01_creation_base_et_roles.sql   rôles et base
│   ├── 02_tables.sql                   schémas, tables, contraintes, vue de modélisation, dictionnaire
│   ├── 03_index.sql                    clés primaires et index
│   └── 04_securite.sql                 droits par rôle et vue anonymisée
├── installer_base.py            ← installation complète de la base en une commande
├── charger_donnees.py           ← contrôle qualité et chargement des CSV (COPY)
├── mesurer_performances.py      ← comparaison tables optimisées / non optimisées
├── sauvegarder_base.py          ← dump SQL et test de restauration
├── novascore_dump.sql           ← dump SQL complet de la base
│
├── Scoring_Credit.ipynb         ← notebook : préparation, modélisation, évaluation, explicabilité
├── models/                      ← modèle entraîné, seuil de décision, résultats (produits par le notebook)
├── app.py                       ← application NovaScore (Streamlit)
├── .streamlit/
│   └── secrets.toml.exemple     ← modèle des secrets pour le déploiement en ligne
│
└── rapports/                    ← rapports produits par les scripts
    ├── compte_rendu_chargement.md      contrôle qualité des CSV et vérification du chargement
    ├── dictionnaire_donnees.md         tables, colonnes, types, contraintes, index
    ├── tests_securite.md               15 tests de droits par rôle
    ├── performances.md / .png          temps d'exécution : tables optimisées vs non optimisées
    ├── plans_execution.txt             plans d'exécution PostgreSQL (EXPLAIN ANALYZE)
    └── test_restauration.md            vérification du dump SQL
```

---

## 4. Prérequis

| Composant | Version | Remarque |
|---|---|---|
| Python | 3.10 ou plus (testé avec 3.12 et 3.13) | |
| PostgreSQL | 16 ou plus (testé avec 16 et 18) | installateur : https://www.postgresql.org/download/ |
| pgAdmin 4 | facultatif | installé avec PostgreSQL sous Windows ; utile pour consulter la base |
| Navigateur web | Chrome, Edge, Firefox ou Safari récents | voir la [compatibilité](#10-compatibilité-navigateurs) |

Librairies Python principales (`requirements.txt`) : pandas, scikit-learn, LightGBM, XGBoost, SHAP, Streamlit, Plotly, psycopg (pilote PostgreSQL et réserve de connexions).

---

## 5. Installation locale

Les commandes sont à lancer dans un terminal ouvert dans le dossier du projet.

**1. Installer les librairies Python**

```bash
python -m pip install -r requirements-notebook.txt
```

`requirements.txt` contient les librairies de l'application, aux versions qui ont entraîné le modèle (utilisé pour le déploiement) ; `requirements-notebook.txt` y ajoute celles du notebook et des scripts de la base.

**2. Configurer la connexion à PostgreSQL**

Copier `.env.exemple` en `.env`, puis renseigner `PG_SUPERUTILISATEUR_MDP` (mot de passe de `postgres`). Facultatif : renseigner `ANTHROPIC_API_KEY` pour activer l'assistant d'explication (voir [Assistant d'explication](#assistant-dexplication-ia)). Sous Windows, vérifier que le fichier s'appelle bien `.env` et non `.env.txt`.

**3. Installer la base**

```bash
python installer_base.py
```

Le script crée les rôles et la base, contrôle et charge les CSV, crée les index, applique les droits, crée les comptes de démonstration, puis teste la sécurité. Résultat attendu :

```text
[3/8] …  Jeu de modélisation : 36,457 demandes, 302 défauts graves
[6/8] …  15/15 tests conformes
[8/8] Tables de la base
  sources.application_record      table     438 557
  sources.credit_record           table   1 048 575
  sources.v_donnees_modelisation  vue        36 457
  …
Installation terminée.
```

Le script peut être relancé sans risque : la base existante est conservée et les données sont rechargées. `python installer_base.py --reinitialiser` supprime la base et les rôles, puis réinstalle tout.

**4. Mesurer les performances** (facultatif, environ 30 secondes)

```bash
python mesurer_performances.py
```

**5. Entraîner le modèle**

Ouvrir `Scoring_Credit.ipynb` et tout exécuter (*Run All*). Le notebook lit ses données dans PostgreSQL et écrit le modèle dans `models/`. Durée : quelques minutes.

**6. Lancer l'application**

```bash
streamlit run app.py
```

L'application s'ouvre dans le navigateur à l'adresse http://localhost:8501.

---

### Déploiement en ligne

L'application est déployée sur **Streamlit Community Cloud** à partir du dépôt GitHub ; ses données (schéma `app`) sont dans une base PostgreSQL hébergée par **Neon** (région Europe). Les données sources ne sont pas nécessaires en ligne : le modèle est déjà entraîné (`models/`).

1. **Base en ligne** : créer un projet Neon (région Europe), copier `.env.en_ligne.exemple` en `.env.en_ligne`, y recopier l'hôte (sans `-pooler`) et le mot de passe de `neondb_owner`, choisir trois mots de passe robustes, puis lancer `python installer_base.py --en-ligne` (rôles, schéma `app`, droits, comptes de démonstration, 15 tests de sécurité → `rapports/tests_securite_en_ligne.md`).
2. **Dépôt GitHub** : envoyer le projet ; `.gitignore` exclut les fichiers de configuration et les fichiers volumineux (CSV, dump).
3. **Streamlit Community Cloud** : *Create app* → dépôt, branche `main`, fichier `app.py`, *Advanced settings* → Python 3.13 et secrets (modèle : `.streamlit/secrets.toml.exemple`, rubriques `[postgres]` avec le rôle `novascore_app` et `[assistant]`).
4. **Après le premier lancement** : se connecter en `admin` et remplacer immédiatement son mot de passe (menu Utilisateurs).

## 6. Utilisation

| Page | Contenu |
|---|---|
| **Connexion** | Identifiant et mot de passe (voir [Identifiants](#2-identifiants)). |
| **Tableau de bord** | Indicateurs (demandes, accords, refus, décisions différentes de la recommandation) et graphiques, filtrables par période. |
| **Nouvelle demande** | Formulaire (champs obligatoires signalés en rouge), bouton *Évaluer la demande* : score sur 100, recommandation, facteurs qui augmentent ou diminuent le risque avec leur valeur SHAP, explication de l'assistant. L'analyste confirme ou modifie la décision, ajoute un commentaire, puis enregistre. |
| **Historique** | Liste filtrable (période, décision, recherche), export CSV, détail d'une demande (facteurs avec leur valeur SHAP), correction d'une décision avec motif obligatoire et suivi des décisions. |
| **Utilisateurs** | Administrateurs uniquement : création, modification (identifiant, rôle, mot de passe) et suppression des comptes. |

### Lire l'explication du score

Chaque facteur affiche sa **valeur SHAP** : le signe donne le sens (+ augmente le risque, − le diminue), la taille donne la force. Les niveaux sont fixes, identiques pour tous les dossiers :

| Valeur (en valeur absolue) | Niveau |
|---|---|
| 1 ou plus | Très fort |
| de 0,5 à 1 | Fort |
| de 0,2 à 0,5 | Modéré |
| moins de 0,2 | Faible |

Le score part de celui d'un client moyen (environ 5 / 100) ; la somme des valeurs SHAP du dossier le fait monter ou descendre jusqu'au score affiché.

### Assistant d'explication (IA)

Sous « Pourquoi ce score ? », l'assistant rédige en langage simple : **Comprendre le score**, **Points à vérifier** et une **Proposition de commentaire**, que l'analyste peut reprendre d'un clic puis modifier avant d'enregistrer.

- **Il ne décide rien** : il explique le score, il ne dit jamais si le client est solvable et ne recommande aucune décision.
- **Modèle** : Claude (Anthropic), `claude-haiku-5-5` par défaut, appelé par l'API Messages.
- **Données transmises** : uniquement le score, le seuil et les facteurs (libellé métier, valeur SHAP, niveau). Aucun nom, identifiant, référence de dossier ni compte utilisateur.
- **Réponse contrôlée** : elle est refusée si elle cite un chiffre absent des données, emploie une formule de décision (accord, refus, solvable…) ou présente un facteur comme une cause. Elle est alors remplacée par l'**explication automatique**, rédigée par des règles fixes à partir des mêmes données.
- **Sans clé d'API**, ou si le service ne répond pas, l'application fonctionne normalement avec l'explication automatique (badge « Explication automatique »).
- **Traçabilité** : quand le commentaire enregistré reprend une proposition de l'IA, le journal le mentionne avec le modèle utilisé (`app.journal.assistance_ia`, `modele_ia`), et l'historique l'affiche.

Configuration : `ANTHROPIC_API_KEY` dans `.env` (poste local) ou rubrique `[assistant]` des secrets Streamlit (en ligne), voir `.streamlit/secrets.toml.exemple`.

---

## 7. Base de données

La base `novascore` est organisée en trois schémas :

| Schéma | Contenu |
|---|---|
| `sources` | Données des CSV, typées et contrôlées (contraintes `CHECK`), avec clés et index. Vue `v_donnees_modelisation` : jeu de modélisation et variable cible. |
| `performance` | Mêmes données, tout en texte, sans clé ni index : tables non optimisées, pour la comparaison des temps d'exécution. |
| `app` | Données de l'application : `utilisateurs`, `demandes`, `journal` (suivi des décisions), vue `v_demandes_anonymisees`. |

**Afficher les tables (pgAdmin)** : Servers → PostgreSQL → Databases → `novascore` → Schemas → `sources` → Tables, puis clic droit sur une table → *View/Edit Data* → *First 100 Rows*.

**Afficher les tables (psql)** :

```sql
\c novascore
\dt sources.*
SELECT * FROM sources.application_record LIMIT 10;
SELECT * FROM sources.v_donnees_modelisation LIMIT 10;
```

Le dictionnaire de données complet est dans `rapports/dictionnaire_donnees.md`.

**Optimisation.** Les requêtes ciblées (une demande, l'historique d'un client) passent de plusieurs dizaines de millisecondes à moins d'un dixième de milliseconde grâce aux index. Détail, plans d'exécution et graphique : `rapports/performances.md`.

---

## 8. Restaurer la base depuis le dump SQL

`novascore_dump.sql` contient toute la base : schémas, tables, vues, index, contraintes, commentaires, droits et données. Il est produit et vérifié par `python sauvegarder_base.py` (rapport : `rapports/test_restauration.md`).

Le dump est produit avec PostgreSQL 18 : il se restaure sur un serveur PostgreSQL **18 ou plus récent**. Sur une version plus ancienne, installer la base à partir des CSV avec `python installer_base.py` (compatible PostgreSQL 16 et plus) : le résultat est identique.

Sur un poste où la base n'existe pas, deux étapes suffisent.

**1. Créer les rôles et la base vide** (avec le super-utilisateur) :

```bash
psql -U postgres -d postgres -v mdp_admin='NsAdmin_2026!' -v mdp_app='NsApp_2026!' -v mdp_lecture='NsLecture_2026!' -f sql/01_creation_base_et_roles.sql
```

**2. Restaurer le dump** (avec le propriétaire de la base) :

```bash
psql -U novascore_admin -d novascore -v ON_ERROR_STOP=1 -f novascore_dump.sql
```

Sous Windows, `psql` se trouve dans `C:\Program Files\PostgreSQL\<version>\bin` s'il n'est pas dans le PATH. Avec pgAdmin : clic droit sur la base `novascore` → *Query Tool* → ouvrir `novascore_dump.sql` → exécuter.

---

## 9. Accès administrateur (back office)

- **Application** : se connecter avec le compte administrateur. Le menu **Utilisateurs** permet de créer, modifier et supprimer les comptes et d'attribuer les rôles. Un analyste ne voit pas ce menu, et la page lui est refusée même par accès direct.
- **Base de données** : pgAdmin ou psql avec le rôle `novascore_admin` (propriétaire de la base) pour consulter et administrer toutes les tables.

---

## 10. Compatibilité navigateurs

| Navigateur | Version testée | Résultat |
|---|---|---|
| Google Chrome | *à compléter* | *à compléter* |
| Microsoft Edge | *à compléter* | *à compléter* |
| Mozilla Firefox | *à compléter* | *à compléter* |
| Safari | *à compléter* | *à compléter* |

Parcours vérifié sur chaque navigateur : connexion, tableau de bord, nouvelle demande (validation, évaluation, enregistrement), historique (filtres, correction), gestion des utilisateurs, déconnexion.

---

## 11. Sécurité et protection des données

- **Mots de passe de l'application** : jamais stockés en clair (PBKDF2-SHA256, 200 000 itérations, sel aléatoire par compte).
- **Mots de passe de la base** : chiffrés par PostgreSQL (SCRAM-SHA-256), lus dans `.env` en local ou dans les secrets Streamlit en ligne, jamais écrits dans le code ni versionnés (`.gitignore`).
- **Moindre privilège** : trois rôles aux droits distincts, vérifiés par 15 tests automatiques (`rapports/tests_securite.md`).
- **Traçabilité** : chaque décision et chaque correction (auteur, date, motif) est inscrite dans `app.journal`, que l'application ne peut ni modifier ni supprimer.
- **Minimisation** : la vue `app.v_demandes_anonymisees` donne des tranches d'âge et de revenu, sans données exactes, pour le reporting.
- **Requêtes paramétrées** : aucune donnée saisie n'est insérée directement dans le texte d'une requête SQL (protection contre l'injection SQL).
- **Décision humaine** : le modèle recommande, l'analyste décide.
- **Assistant IA** : seuls le score, le seuil et les facteurs explicatifs sont envoyés à l'API Claude (Anthropic, traitement hors Union européenne), sans aucune donnée d'identification ; l'assistant explique et ne décide pas ; ses réponses sont contrôlées et toute reprise dans un commentaire est tracée. La clé d'API est un secret, jamais versionné.

---

## 12. Dépannage

| Message | Cause | Solution |
|---|---|---|
| `authentification par mot de passe échouée pour l'utilisateur « postgres »` | Le mot de passe dans `.env` ne correspond pas à celui de `postgres`. | Dans pgAdmin : `ALTER USER postgres PASSWORD 'nouveau';`, puis reporter la même valeur dans `.env`. |
| `Fichier .env introuvable` | Le fichier s'appelle `.env.txt` ou n'est pas dans le dossier du projet. | Renommer en `.env`, à côté de `installer_base.py`. |
| `No such file or directory: 'application_record.csv'` | Les CSV ne sont pas dans le dossier du projet. | Copier les deux CSV à côté de `installer_base.py`. |
| `No module named 'psycopg'` dans le notebook | Le notebook utilise un autre environnement Python. | Exécuter `%pip install "psycopg[binary,pool]"` dans une cellule, puis redémarrer le noyau. |
| `name 'psycopg' is not defined` | Les cellules n'ont pas été exécutées dans l'ordre. | *Run All* depuis le début du notebook. |
| « Service momentanément indisponible » dans l'application | Base arrêtée ou mot de passe de `novascore_app` incorrect. | Vérifier que PostgreSQL est démarré et la valeur de `NOVASCORE_APP_MDP` dans `.env`. |
| L'assistant affiche « Explication automatique » | Aucune clé d'API, clé invalide, crédit épuisé ou service indisponible. | Vérifier `ANTHROPIC_API_KEY` dans `.env` (ou les secrets en ligne) et le crédit du compte Anthropic. L'application reste utilisable. |
| pgAdmin refuse la connexion après un changement de mot de passe | pgAdmin a gardé l'ancien mot de passe. | Clic droit sur le serveur → *Clear Saved Password*, puis se reconnecter. |
