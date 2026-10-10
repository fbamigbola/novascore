"""NovaScore — chargement des fichiers CSV dans PostgreSQL.

1. Contrôle la qualité des deux fichiers sources (volumétrie, valeurs manquantes, doublons, incohérences).
2. Charge les données dans les tables du schéma « sources » (typées) avec la commande COPY.
3. Charge les mêmes données dans les tables du schéma « performance » (non optimisées).
4. Vérifie que la base contient exactement le même nombre de lignes que les fichiers.
5. Écrit un compte rendu dans rapports/compte_rendu_chargement.md.

Utilisation : python charger_donnees.py   (lancé automatiquement par installer_base.py)
Pré-requis : la base et les tables existent (sql/01 et sql/02), le fichier .env est rempli.
"""
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd
import psycopg

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_DEMANDES = os.path.join(DOSSIER, "application_record.csv")
FICHIER_HISTORIQUE = os.path.join(DOSSIER, "credit_record.csv")
DOSSIER_RAPPORTS = os.path.join(DOSSIER, "rapports")


def lire_env():
    """Lit le fichier .env (lignes CLE=valeur) sans dépendance externe."""
    chemin = os.path.join(DOSSIER, ".env")
    if not os.path.exists(chemin):
        sys.exit("Fichier .env introuvable : copiez .env.exemple en .env et complétez-le.")
    valeurs = {}
    with open(chemin, encoding="utf-8-sig") as f:  # -sig : ignore l'en-tête ajouté par le Bloc-notes
        for ligne in f:
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                valeur = valeur.strip()
                if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
                    valeur = valeur[1:-1]  # guillemets autour de la valeur : retirés
                valeurs[cle.strip()] = valeur
    return valeurs


def connecter(env, utilisateur, mot_de_passe, base):
    return psycopg.connect(host=env.get("PG_HOTE", "localhost"), port=env.get("PG_PORT", "5432"),
                           dbname=base, user=utilisateur, password=mot_de_passe,
                           sslmode=env.get("PG_SSLMODE", "prefer"))


def taille_mo(chemin):
    return f"{os.path.getsize(chemin) / 1e6:.1f} Mo".replace(".", ",")


def pourcentage(x, decimales):
    return f"{x * 100:.{decimales}f} %".replace(".", ",")


def controler_qualite(demandes, historique):
    """Contrôles de qualité avant chargement. Renvoie des lignes de compte rendu (Markdown)."""
    cr = ["## 1. Volumétrie des sources", "",
          "| Fichier | Lignes | Colonnes | Taille |", "|---|---|---|---|",
          f"| application_record.csv | {len(demandes):,} | {demandes.shape[1]} | {taille_mo(FICHIER_DEMANDES)} |",
          f"| credit_record.csv | {len(historique):,} | {historique.shape[1]} | {taille_mo(FICHIER_HISTORIQUE)} |",
          ""]

    cr += ["## 2. Valeurs manquantes", "", "| Fichier | Colonne | Valeurs manquantes | Part |", "|---|---|---|---|"]
    for nom, df in (("application_record.csv", demandes), ("credit_record.csv", historique)):
        manquantes = df.isna().sum()
        for colonne, n in manquantes[manquantes > 0].items():
            cr.append(f"| {nom} | {colonne} | {n:,} | {pourcentage(n / len(df), 1)} |")
        if manquantes.sum() == 0:
            cr.append(f"| {nom} | (aucune) | 0 | 0 % |")
    cr.append("")

    ids_doublons = demandes.loc[demandes["ID"].duplicated(), "ID"].nunique()
    profils = demandes.drop(columns="ID").duplicated().sum()
    clients_historique = historique["ID"].nunique()
    sans_historique = (~demandes["ID"].isin(historique["ID"])).sum()
    historique_sans_demande = (~historique["ID"].drop_duplicates().isin(demandes["ID"])).sum()
    cr += ["## 3. Doublons et cohérence entre les fichiers", "",
           "| Contrôle | Résultat |", "|---|---|",
           f"| Lignes entièrement identiques (demandes) | {demandes.duplicated().sum():,} |",
           f"| Identifiants client présents plusieurs fois (demandes) | {ids_doublons:,} |",
           f"| Demandes au profil identique à une autre (hors identifiant) | {profils:,} |",
           f"| Couples (client, mois) en double (historique) | {historique.duplicated(['ID', 'MONTHS_BALANCE']).sum():,} |",
           f"| Clients distincts dans l'historique | {clients_historique:,} |",
           f"| Demandes sans aucun historique de crédit | {sans_historique:,} |",
           f"| Clients de l'historique sans demande | {historique_sans_demande:,} |",
           ""]

    cr += ["## 4. Incohérences et valeurs particulières", "",
           "| Contrôle | Résultat | Traitement |", "|---|---|---|",
           f"| `DAYS_EMPLOYED` = 365243 (code « sans emploi », environ 1 000 ans) | {(demandes['DAYS_EMPLOYED'] == 365243).sum():,} lignes | Indicateur SANS_EMPLOI et ancienneté à 0 (notebook, étape 6) |",
           f"| `FLAG_MOBIL` constant | valeurs : {sorted(demandes['FLAG_MOBIL'].unique().tolist())} | Variable retirée du modèle |",
           f"| Revenu annuel nul ou négatif | {(demandes['AMT_INCOME_TOTAL'] <= 0).sum():,} | Contrôlé par une contrainte CHECK |",
           f"| Revenu annuel minimum / maximum | {demandes['AMT_INCOME_TOTAL'].min():,.0f} / {demandes['AMT_INCOME_TOTAL'].max():,.0f} | Valeurs extrêmes conservées |",
           f"| Nombre d'enfants maximum | {demandes['CNT_CHILDREN'].max()} | Valeur extrême conservée |",
           f"| Âge minimum / maximum (années) | {-demandes['DAYS_BIRTH'].max() / 365:.0f} / {-demandes['DAYS_BIRTH'].min() / 365:.0f} | Cohérent |",
           f"| Statuts de l'historique | {', '.join(sorted(historique['STATUS'].astype(str).unique()))} | Contrôlé par une contrainte CHECK |",
           f"| Mois de l'historique | de {historique['MONTHS_BALANCE'].min()} à {historique['MONTHS_BALANCE'].max()} | Contrôlé par une contrainte CHECK |",
           ""]
    return cr


def copier(curseur, table, colonnes, donnees_csv):
    """Charge un texte CSV (sans en-tête) dans une table avec COPY, par blocs."""
    requete = f"COPY {table} ({', '.join(colonnes)}) FROM STDIN WITH (FORMAT csv)"
    with curseur.copy(requete) as copie:
        for i in range(0, len(donnees_csv), 1_000_000):
            copie.write(donnees_csv[i:i + 1_000_000])


def main():
    env = lire_env()
    debut = time.perf_counter()
    print("Lecture des fichiers CSV…")
    demandes = pd.read_csv(FICHIER_DEMANDES)
    historique = pd.read_csv(FICHIER_HISTORIQUE, dtype={"STATUS": str})
    compte_rendu = [f"# Compte rendu du chargement des données — {datetime.now():%d/%m/%Y %H:%M}", ""]
    compte_rendu += controler_qualite(demandes, historique)

    # Mise en forme pour les tables typées
    demandes_bd = demandes.rename(columns=str.lower)
    demandes_bd["cnt_fam_members"] = demandes_bd["cnt_fam_members"].astype(int)
    historique_bd = historique.rename(columns=str.lower)

    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], env.get("NOVASCORE_BASE", "novascore")) as con:
        with con.cursor() as cur:
            print("Chargement de sources.application_record…")
            cur.execute("TRUNCATE sources.application_record RESTART IDENTITY")
            copier(cur, "sources.application_record", list(demandes_bd.columns),
                   demandes_bd.to_csv(index=False, header=False))
            print("Chargement de sources.credit_record…")
            cur.execute("TRUNCATE sources.credit_record")
            copier(cur, "sources.credit_record", list(historique_bd.columns),
                   historique_bd.to_csv(index=False, header=False))

            print("Chargement des tables non optimisées (schéma performance)…")
            for table, fichier in (("performance.application_record_non_optimisee", FICHIER_DEMANDES),
                                   ("performance.credit_record_non_optimisee", FICHIER_HISTORIQUE)):
                cur.execute(f"TRUNCATE {table}")
                with open(fichier, encoding="utf-8") as f:
                    entete = f.readline().strip().lower().split(",")
                    copier(cur, table, entete, f.read())

            verifications = []
            for table, attendu in (("sources.application_record", len(demandes)),
                                   ("sources.credit_record", len(historique)),
                                   ("performance.application_record_non_optimisee", len(demandes)),
                                   ("performance.credit_record_non_optimisee", len(historique))):
                n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                verifications.append((table, attendu, n))
            cible = cur.execute("SELECT COUNT(*), SUM(defaut) FROM sources.v_donnees_modelisation").fetchone()
        con.commit()

    compte_rendu += ["## 5. Vérification après chargement", "",
                     "| Table | Lignes attendues | Lignes en base | Résultat |", "|---|---|---|---|"]
    for table, attendu, n in verifications:
        compte_rendu.append(f"| {table} | {attendu:,} | {n:,} | {'conforme' if attendu == n else 'ÉCART'} |")
    compte_rendu += ["", "## 6. Jeu de données de modélisation (vue sources.v_donnees_modelisation)", "",
                     f"- Demandes conservées (avec historique, sans identifiant en double) : **{cible[0]:,}**",
                     f"- Défauts graves (statut 3, 4 ou 5) : **{cible[1]:,}**, soit **{pourcentage(cible[1] / cible[0], 2)}**",
                     "", f"Durée totale du chargement : {time.perf_counter() - debut:.1f} s.".replace(".", ",", 1)]

    os.makedirs(DOSSIER_RAPPORTS, exist_ok=True)
    chemin = os.path.join(DOSSIER_RAPPORTS, "compte_rendu_chargement.md")
    with open(chemin, "w", encoding="utf-8") as f:
        # séparateur de milliers à la française : 438,557 → 438 557
        f.write(re.sub(r"(?<=\d),(?=\d{3})", " ", "\n".join(compte_rendu)) + "\n")
    for table, attendu, n in verifications:
        print(f"  {table} : {n:,} lignes ({'conforme' if attendu == n else 'ÉCART'})")
    print(f"  Jeu de modélisation : {cible[0]:,} demandes, {cible[1]} défauts graves")
    print(f"Compte rendu écrit dans {os.path.relpath(chemin, DOSSIER)}")
    if any(attendu != n for _, attendu, n in verifications):
        sys.exit("Erreur : le nombre de lignes chargées ne correspond pas aux fichiers.")


if __name__ == "__main__":
    main()
