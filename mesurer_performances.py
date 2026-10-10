"""NovaScore — comparaison des temps d'exécution : tables non optimisées vs tables optimisées.

Les mêmes requêtes sont exécutées :
  - sur les tables du schéma « performance » : tout en texte, sans clé ni index ;
  - sur les tables du schéma « sources » : colonnes typées, clé primaire et index (sql/03_index.sql).

Chaque requête est exécutée une première fois (mise en cache), puis 15 fois. On retient la médiane du temps
d'exécution mesuré par PostgreSQL (EXPLAIN ANALYZE). Le script produit :
  - rapports/performances.md    : tableau des temps, gains et méthodes d'accès utilisées ;
  - rapports/performances.png   : graphique comparatif ;
  - rapports/plans_execution.txt : plans d'exécution complets (EXPLAIN ANALYZE).

Utilisation : python mesurer_performances.py   (après installer_base.py)
"""
import json
import os
import statistics
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psycopg

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_RAPPORTS = os.path.join(DOSSIER, "rapports")
REPETITIONS = 15
CLIENT = 5008804  # identifiant client utilisé pour les recherches ciblées

REQUETES = [
    ("Recherche d'une demande par identifiant client",
     f"SELECT * FROM performance.application_record_non_optimisee WHERE id = '{CLIENT}'",
     f"SELECT * FROM sources.application_record WHERE id = {CLIENT}"),
    ("Historique de remboursement d'un client",
     f"SELECT months_balance, status FROM performance.credit_record_non_optimisee "
     f"WHERE id = '{CLIENT}' ORDER BY months_balance::INTEGER",
     f"SELECT months_balance, status FROM sources.credit_record WHERE id = {CLIENT} ORDER BY months_balance"),
    ("Liste des clients en défaut grave (statut 3, 4 ou 5)",
     "SELECT DISTINCT id FROM performance.credit_record_non_optimisee WHERE status IN ('3', '4', '5')",
     "SELECT DISTINCT id FROM sources.credit_record WHERE status IN ('3', '4', '5')"),
    ("Construction du jeu de modélisation (variable cible)",
     """WITH ids_doublons AS (SELECT id FROM performance.application_record_non_optimisee GROUP BY id HAVING COUNT(*) > 1),
             historique AS (SELECT DISTINCT id FROM performance.credit_record_non_optimisee),
             defauts AS (SELECT DISTINCT id FROM performance.credit_record_non_optimisee WHERE status IN ('3', '4', '5'))
        SELECT COUNT(*), SUM(CASE WHEN d.id IS NULL THEN 0 ELSE 1 END)
        FROM performance.application_record_non_optimisee a
        JOIN historique h ON h.id = a.id LEFT JOIN defauts d ON d.id = a.id
        WHERE a.id NOT IN (SELECT id FROM ids_doublons)""",
     "SELECT COUNT(*), SUM(defaut) FROM sources.v_donnees_modelisation"),
]


def lire_env():
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


def types_de_noeuds(plan):
    """Liste des opérations d'un plan d'exécution JSON (parcours récursif)."""
    noeuds = [plan["Node Type"]]
    for enfant in plan.get("Plans", []):
        noeuds += types_de_noeuds(enfant)
    return noeuds


def acces(noeuds):
    """Résume la façon dont les tables sont lues."""
    lectures = [n for n in noeuds if "Scan" in n and n not in ("CTE Scan", "Subquery Scan")]
    if not lectures:
        return "—"
    return ", ".join(sorted(set(lectures)))


def mesurer(curseur, requete):
    curseur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + requete)  # première exécution : mise en cache
    temps = []
    plan = None
    for _ in range(REPETITIONS):
        resultat = curseur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + requete).fetchone()[0]
        resultat = resultat if isinstance(resultat, list) else json.loads(resultat)
        temps.append(resultat[0]["Execution Time"])
        plan = resultat[0]["Plan"]
    texte = "\n".join(r[0] for r in curseur.execute("EXPLAIN (ANALYZE, BUFFERS) " + requete).fetchall())
    return statistics.median(temps), acces(types_de_noeuds(plan)), texte


def main():
    env = lire_env()
    con = psycopg.connect(host=env.get("PG_HOTE", "localhost"), port=env.get("PG_PORT", "5432"),
                          dbname=env.get("NOVASCORE_BASE", "novascore"), user="novascore_admin",
                          password=env["NOVASCORE_ADMIN_MDP"], sslmode=env.get("PG_SSLMODE", "prefer"),
                          autocommit=True)
    cur = con.cursor()
    version = cur.execute("SHOW server_version").fetchone()[0]

    tailles = cur.execute("""
        SELECT n.nspname || '.' || c.relname, c.reltuples::BIGINT,
               pg_size_pretty(pg_table_size(c.oid)), pg_size_pretty(pg_indexes_size(c.oid))
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('sources', 'performance') AND c.relkind = 'r' ORDER BY 1""").fetchall()

    resultats, plans = [], []
    for libelle, non_optimisee, optimisee in REQUETES:
        print(f"Mesure : {libelle}…")
        t_non, acces_non, plan_non = mesurer(cur, non_optimisee)
        t_opt, acces_opt, plan_opt = mesurer(cur, optimisee)
        resultats.append((libelle, t_non, t_opt, acces_non, acces_opt))
        plans.append(f"===== {libelle}\n\n--- Table non optimisée ---\n{non_optimisee}\n\n{plan_non}\n\n"
                     f"--- Table optimisée ---\n{optimisee}\n\n{plan_opt}\n")
    con.close()

    os.makedirs(DOSSIER_RAPPORTS, exist_ok=True)
    def fr(x):
        """Nombre à la française ; 3 décimales sous 1 ms pour garder la précision."""
        return f"{x:,.{3 if x < 1 else 2}f}".replace(",", " ").replace(".", ",")

    def gain(t_non, t_opt):
        return f"× {t_non / t_opt:,.0f}".replace(",", " ") if t_non / t_opt >= 10 else f"× {fr(t_non / t_opt)}"
    lignes = [f"# Comparaison des temps d'exécution — {datetime.now():%d/%m/%Y %H:%M}", "",
              f"PostgreSQL {version}. Médiane de {REPETITIONS} exécutions après une première exécution de mise en cache, "
              "temps d'exécution mesuré par le serveur (`EXPLAIN ANALYZE`).", "",
              "## Tables comparées", "",
              "| Table | Lignes | Taille des données | Taille des index |", "|---|---|---|---|"]
    lignes += [f"| {t} | {n:,} | {d} | {i} |".replace(",", " ") for t, n, d, i in tailles]
    lignes += ["", "## Résultats", "",
               "| Requête | Non optimisée (ms) | Optimisée (ms) | Gain | Lecture non optimisée | Lecture optimisée |",
               "|---|---|---|---|---|---|"]
    for libelle, t_non, t_opt, a_non, a_opt in resultats:
        lignes.append(f"| {libelle} | {fr(t_non)} | {fr(t_opt)} | {gain(t_non, t_opt)} | {a_non} | {a_opt} |")
    lignes += ["", "**Lecture :** *Seq Scan* = lecture complète de la table, ligne par ligne ; "
               "*Index Scan* / *Index Only Scan* = accès direct aux lignes utiles grâce à un index ; "
               "*Bitmap Index/Heap Scan* = index utilisé pour repérer un ensemble de lignes, puis lecture de ces lignes.",
               "", "## Interprétation", "",
               "- **Requêtes ciblées** (une demande, l'historique d'un client, les clients en défaut) : sans index, "
               "PostgreSQL lit toute la table pour trouver quelques lignes. Avec l'index, il accède directement aux lignes "
               "utiles : le temps passe de plusieurs dizaines de millisecondes à moins d'une milliseconde. Ce sont les "
               "recherches typiques de la consultation d'un dossier client.",
               "- **Construction du jeu de modélisation** : la requête doit parcourir l'ensemble de l'historique "
               "(plus d'un million de lignes) pour savoir quels clients ont un historique. Un index n'évite pas une "
               "lecture complète : le gain est faible. Cette requête n'est exécutée qu'une fois, au chargement des "
               "données du notebook ; son temps reste acceptable.",
               "- Contrepartie des index : de l'espace disque supplémentaire (colonne « Taille des index ») et une "
               "écriture un peu plus lente à chaque insertion. Les index sont donc créés seulement pour les recherches réellement utilisées."]
    with open(os.path.join(DOSSIER_RAPPORTS, "performances.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")
    with open(os.path.join(DOSSIER_RAPPORTS, "plans_execution.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(plans))

    # Graphique : barres horizontales, échelle logarithmique (les écarts vont de x2 à x1000)
    fig, ax = plt.subplots(figsize=(10, 5.4))
    y = range(len(resultats))
    hauteur = 0.38
    b1 = ax.barh([i - hauteur / 2 for i in y], [r[1] for r in resultats], height=hauteur, color="#9AA5B1", label="Table non optimisée")
    b2 = ax.barh([i + hauteur / 2 for i in y], [r[2] for r in resultats], height=hauteur, color="#1F5FA8", label="Table optimisée (typée + index)")
    ax.set_xscale("log")
    ax.set_yticks(list(y))
    ax.set_yticklabels([r[0] for r in resultats])
    ax.invert_yaxis()
    ax.bar_label(b1, labels=[fr(r[1]) + " ms" for r in resultats], padding=3, fontsize=9)
    ax.bar_label(b2, labels=[fr(r[2]) + " ms" for r in resultats], padding=3, fontsize=9)
    ax.set_xlabel("Temps d'exécution médian (ms, échelle logarithmique)")
    ax.set_title("Temps d'exécution des requêtes : tables non optimisées vs optimisées")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False)
    for cote in ("top", "right"):
        ax.spines[cote].set_visible(False)
    ax.set_xlim(right=max(r[1] for r in resultats) * 8)
    plt.tight_layout()
    plt.savefig(os.path.join(DOSSIER_RAPPORTS, "performances.png"), dpi=150)

    print("\nRésultats (médiane, ms) :")
    for libelle, t_non, t_opt, a_non, a_opt in resultats:
        print(f"  {libelle}\n    non optimisée {t_non:9.2f} ms ({a_non})\n    optimisée     {t_opt:9.2f} ms ({a_opt})  → gain × {t_non / t_opt:.1f}")
    print("Rapports écrits dans rapports/ (performances.md, performances.png, plans_execution.txt)")


if __name__ == "__main__":
    main()
