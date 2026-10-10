"""NovaScore — sauvegarde (dump SQL) de la base et test de restauration.

1. Exporte toute la base « novascore » (schémas, tables, vues, index, droits, commentaires et données)
   dans le fichier novascore_dump.sql, avec l'outil pg_dump fourni avec PostgreSQL.
2. Vérifie la sauvegarde : la restaure dans une base temporaire « novascore_restauration »,
   compare le nombre de lignes de chaque table et vue avec la base d'origine, puis supprime la base temporaire.
3. Écrit le résultat dans rapports/test_restauration.md.

Utilisation : python sauvegarder_base.py
Restauration sur un autre poste : voir le README (rubrique « Restaurer la base depuis le dump »).
"""
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime

import psycopg
from psycopg import sql

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_DUMP = os.path.join(DOSSIER, "novascore_dump.sql")
BASE_TEST = "novascore_restauration"
OBJETS = ["sources.application_record", "sources.credit_record", "sources.v_donnees_modelisation",
          "performance.application_record_non_optimisee", "performance.credit_record_non_optimisee",
          "app.utilisateurs", "app.demandes", "app.journal", "app.v_demandes_anonymisees"]


def lire_env():
    chemin = os.path.join(DOSSIER, ".env")
    if not os.path.exists(chemin):
        sys.exit("Fichier .env introuvable : copiez .env.exemple en .env et complétez-le.")
    valeurs = {}
    with open(chemin, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                valeurs[cle.strip()] = valeur.strip().strip('"').strip("'")
    return valeurs


def trouver_outil(nom):
    """Cherche pg_dump / psql dans le PATH, puis dans les dossiers d'installation habituels (Windows, macOS, Linux)."""
    trouve = shutil.which(nom)
    if trouve:
        return trouve
    motifs = [r"C:\Program Files\PostgreSQL\*\bin\{}.exe", "/Library/PostgreSQL/*/bin/{}",
              "/Applications/Postgres.app/Contents/Versions/*/bin/{}", "/usr/lib/postgresql/*/bin/{}"]
    candidats = [c for m in motifs for c in glob.glob(m.format(nom))]
    if not candidats:
        sys.exit(f"Outil {nom} introuvable. Ajoutez le dossier bin de PostgreSQL au PATH.")
    # version la plus récente (le numéro de version est dans le chemin)
    return sorted(candidats, key=lambda c: [int(x) for x in os.path.normpath(c).split(os.sep) if x.isdigit()] or [0])[-1]


def connecter(env, utilisateur, mot_de_passe, base, autocommit=False):
    return psycopg.connect(host=env.get("PG_HOTE", "localhost"), port=env.get("PG_PORT", "5432"), dbname=base,
                           user=utilisateur, password=mot_de_passe, sslmode=env.get("PG_SSLMODE", "prefer"),
                           autocommit=autocommit)


def compter(env, base):
    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], base) as con:
        return {o: con.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.SQL(o))).fetchone()[0] for o in OBJETS}


def executer(commande, mot_de_passe, env):
    variables = {**os.environ, "PGPASSWORD": mot_de_passe, "PGSSLMODE": env.get("PG_SSLMODE", "prefer"),
                 "PGCLIENTENCODING": "UTF8"}
    resultat = subprocess.run(commande, env=variables, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if resultat.returncode != 0:
        sys.exit(f"Échec de {os.path.basename(commande[0])} :\n{resultat.stderr[-2000:]}")
    return resultat


def main():
    env = lire_env()
    base = env.get("NOVASCORE_BASE", "novascore")
    hote, port = env.get("PG_HOTE", "localhost"), env.get("PG_PORT", "5432")
    pg_dump, psql = trouver_outil("pg_dump"), trouver_outil("psql")
    super_utilisateur, super_mdp = env.get("PG_SUPERUTILISATEUR", "postgres"), env["PG_SUPERUTILISATEUR_MDP"]

    # 1. Sauvegarde
    print(f"[1/3] Sauvegarde de la base « {base} » avec {pg_dump}")
    executer([pg_dump, "-h", hote, "-p", port, "-U", "novascore_admin", "-d", base,
              "--encoding=UTF8",
              "-f", FICHIER_DUMP], env["NOVASCORE_ADMIN_MDP"], env)
    taille = os.path.getsize(FICHIER_DUMP) / 1e6
    print(f"  {os.path.basename(FICHIER_DUMP)} écrit ({taille:.1f} Mo)")

    # 2. Restauration dans une base temporaire
    print(f"[2/3] Restauration dans la base temporaire « {BASE_TEST} »")
    with connecter(env, super_utilisateur, super_mdp, "postgres", autocommit=True) as con:
        con.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(BASE_TEST)))
        con.execute(sql.SQL("CREATE DATABASE {} OWNER novascore_admin ENCODING 'UTF8' TEMPLATE template0")
                    .format(sql.Identifier(BASE_TEST)))
    executer([psql, "-h", hote, "-p", port, "-U", "novascore_admin", "-d", BASE_TEST, "-q",
              "-v", "ON_ERROR_STOP=1", "-f", FICHIER_DUMP], env["NOVASCORE_ADMIN_MDP"], env)

    # 3. Comparaison des volumes
    print("[3/3] Comparaison avec la base d'origine")
    origine, restauree = compter(env, base), compter(env, BASE_TEST)
    with connecter(env, super_utilisateur, super_mdp, "postgres", autocommit=True) as con:
        con.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(BASE_TEST)))

    conformes = sum(origine[o] == restauree[o] for o in OBJETS)
    lignes = [f"# Test de restauration du dump SQL — {datetime.now():%d/%m/%Y %H:%M}", "",
              f"Fichier : `novascore_dump.sql` ({taille:.1f} Mo), produit par `pg_dump`.", "",
              f"Méthode : restauration complète dans une base vierge (`{BASE_TEST}`) avec `psql`, arrêt à la première "
              "erreur (`ON_ERROR_STOP`), puis comparaison du nombre de lignes de chaque table et vue. "
              "La base temporaire est supprimée après le test.", "",
              "| Table ou vue | Base d'origine | Base restaurée | Conforme |", "|---|---|---|---|"]
    for o in OBJETS:
        lignes.append(f"| {o} | {origine[o]:,} | {restauree[o]:,} | {'oui' if origine[o] == restauree[o] else '**NON**'} |"
                      .replace(",", " "))
    lignes += ["", f"**Résultat : {conformes} objets conformes sur {len(OBJETS)}.**"]
    os.makedirs(os.path.join(DOSSIER, "rapports"), exist_ok=True)
    with open(os.path.join(DOSSIER, "rapports", "test_restauration.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")
    for o in OBJETS:
        print(f"  {'OK ' if origine[o] == restauree[o] else 'ÉCART'} {o:48} {restauree[o]:>10,}".replace(",", " "))
    print(f"  {conformes}/{len(OBJETS)} conformes — rapport : rapports/test_restauration.md")
    if conformes != len(OBJETS):
        sys.exit("La restauration ne reproduit pas la base d'origine.")


if __name__ == "__main__":
    main()
