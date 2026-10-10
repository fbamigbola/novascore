"""NovaScore — installation complète de la base PostgreSQL, en une commande.

Étapes :
  1. Création des trois rôles et de la base « novascore »   (sql/01, super-utilisateur postgres)
  2. Création des schémas, tables, vues et commentaires       (sql/02, rôle novascore_admin)
  3. Contrôle qualité et chargement des CSV                   (charger_donnees.py)
  4. Clés primaires, index et statistiques                    (sql/03)
  5. Droits par rôle, vue anonymisée et comptes de démonstration (sql/04)
  6. Tests de sécurité par rôle                               → rapports/tests_securite.md
  7. Export du dictionnaire de données                        → rapports/dictionnaire_donnees.md
  8. Affichage des tables créées

Utilisation :
  python installer_base.py                 installation (la base existante est conservée)
  python installer_base.py --reinitialiser supprime la base et les rôles, puis réinstalle tout
  python installer_base.py --en-ligne      base hébergée (Neon…) : paramètres lus dans .env.en_ligne ;
                                           pas de chargement des CSV (l'application n'utilise que le schéma app)
"""
import hashlib
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime

import psycopg
from psycopg import sql

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_SQL = os.path.join(DOSSIER, "sql")
DOSSIER_RAPPORTS = os.path.join(DOSSIER, "rapports")
ROLES = ("novascore_admin", "novascore_app", "novascore_lecture")


def lire_env(fichier=".env"):
    """Lit le fichier .env (lignes CLE=valeur) sans dépendance externe."""
    chemin = os.path.join(DOSSIER, fichier)
    if not os.path.exists(chemin):
        sys.exit(f"Fichier {fichier} introuvable : copiez {fichier}.exemple en {fichier} et complétez-le.")
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
    manquantes = [c for c in ("PG_SUPERUTILISATEUR_MDP", "NOVASCORE_ADMIN_MDP", "NOVASCORE_APP_MDP",
                              "NOVASCORE_LECTURE_MDP") if not valeurs.get(c) or valeurs[c].startswith("<")]
    if manquantes:
        sys.exit("Valeurs à compléter dans .env : " + ", ".join(manquantes))
    return valeurs


def connecter(env, utilisateur, mot_de_passe, base, autocommit=False):
    return psycopg.connect(host=env.get("PG_HOTE", "localhost"), port=env.get("PG_PORT", "5432"),
                           dbname=base, user=utilisateur, password=mot_de_passe,
                           sslmode=env.get("PG_SSLMODE", "prefer"), autocommit=autocommit,
                           connect_timeout=10)


def lire_sql(nom):
    with open(os.path.join(DOSSIER_SQL, nom), encoding="utf-8") as f:
        return f.read()


def etape(numero, texte):
    print(f"\n[{numero}/8] {texte}")


# --------------------------------------------------------------------------- 1. base et rôles
def creer_base_et_roles(env, reinitialiser):
    base = env.get("NOVASCORE_BASE", "novascore")
    with connecter(env, env.get("PG_SUPERUTILISATEUR", "postgres"), env["PG_SUPERUTILISATEUR_MDP"],
                   env.get("PG_BASE_MAINTENANCE", "postgres"), autocommit=True) as con:
        cur = con.cursor()
        # Hébergeur (Neon…) : le compte fourni n'est pas super-utilisateur. Pour créer une base dont novascore_admin
        # est propriétaire, il doit être membre de ce rôle (droit obtenu automatiquement en créant le rôle).
        super_utilisateur = cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]
        if reinitialiser:
            print("  Suppression de la base et des rôles existants…")
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(base)))
            for role in ROLES:
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

        if cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone():
            print(f"  La base « {base} » existe déjà : étape ignorée (option --reinitialiser pour tout recréer).")
            return
        existants = [r[0] for r in cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                                               (list(ROLES),)).fetchall()]
        if existants:
            sys.exit(f"  Rôles déjà présents sans la base : {', '.join(existants)}. "
                     "Relancez avec --reinitialiser.")

        # Les variables psql :'mdp_*' sont remplacées par des littéraux SQL correctement échappés.
        script = lire_sql("01_creation_base_et_roles.sql")
        mots_de_passe = {"mdp_admin": env["NOVASCORE_ADMIN_MDP"], "mdp_app": env["NOVASCORE_APP_MDP"],
                         "mdp_lecture": env["NOVASCORE_LECTURE_MDP"]}
        for variable, valeur in mots_de_passe.items():
            script = script.replace(f":'{variable}'", sql.Literal(valeur).as_string(con))
        if base != "novascore":
            script = re.sub(r"\bDATABASE novascore\b", f'DATABASE "{base}"', script)
        # CREATE DATABASE ne peut pas s'exécuter dans une transaction : une instruction à la fois.
        sans_commentaires = "\n".join(l for l in script.splitlines() if not l.strip().startswith("--"))
        for instruction in sans_commentaires.split(";"):
            if not instruction.strip():
                continue
            if instruction.strip().startswith("CREATE DATABASE") and not super_utilisateur:
                cur.execute("GRANT novascore_admin TO CURRENT_USER")
            cur.execute(instruction)
        print(f"  Rôles {', '.join(ROLES)} et base « {base} » créés.")


# --------------------------------------------------------------------------- 2, 4, 5. scripts SQL
def executer_script(env, nom):
    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], env.get("NOVASCORE_BASE", "novascore")) as con:
        con.execute(lire_sql(nom))
        con.commit()
    print(f"  sql/{nom} exécuté.")


# --------------------------------------------------------------------------- 3. chargement
def charger(env):
    resultat = subprocess.run([sys.executable, os.path.join(DOSSIER, "charger_donnees.py")], cwd=DOSSIER)
    if resultat.returncode != 0:
        sys.exit("Le chargement des données a échoué.")


# --------------------------------------------------------------------------- 6. tests de sécurité
def tester_securite(env, fichier="tests_securite.md"):
    base = env.get("NOVASCORE_BASE", "novascore")
    tests = []  # (rôle, action, résultat attendu, résultat obtenu)

    def essai(role, mdp, action, requete, attendu):
        try:
            with connecter(env, role, mdp, base) as con:
                con.execute(requete)
                con.rollback()  # aucun test ne modifie la base
            obtenu = "autorisé"
        except psycopg.Error as e:
            obtenu = "refusé" if attendu == "refusé" else f"refusé ({e.diag.message_primary or e})"
        tests.append((role, action, attendu, obtenu))

    app, lecture = env["NOVASCORE_APP_MDP"], env["NOVASCORE_LECTURE_MDP"]
    essai("novascore_app", app, "Lire les comptes utilisateurs", "SELECT COUNT(*) FROM app.utilisateurs", "autorisé")
    essai("novascore_app", app, "Enregistrer une demande",
          "INSERT INTO app.demandes (reference, date, analyste, donnees, age, revenu, score, recommandation, decision, facteurs)"
          " VALUES ('TEST-SECU', now(), 'test', '{}', 40, 100000, 50, 'accord', 'accord', '[]')",
          "autorisé")
    essai("novascore_app", app, "Supprimer une demande", "DELETE FROM app.demandes", "refusé")
    essai("novascore_app", app, "Modifier le journal de suivi", "UPDATE app.journal SET commentaire = 'x'", "refusé")
    essai("novascore_app", app, "Supprimer le journal de suivi", "DELETE FROM app.journal", "refusé")
    essai("novascore_app", app, "Lire les données sources", "SELECT COUNT(*) FROM sources.application_record", "refusé")
    essai("novascore_app", app, "Lire les tables de performance",
          "SELECT COUNT(*) FROM performance.credit_record_non_optimisee", "refusé")
    essai("novascore_app", app, "Créer une table", "CREATE TABLE app.intrusion (x INT)", "refusé")
    essai("novascore_lecture", lecture, "Lire le jeu de modélisation",
          "SELECT COUNT(*) FROM sources.v_donnees_modelisation", "autorisé")
    essai("novascore_lecture", lecture, "Lire la vue anonymisée des demandes",
          "SELECT COUNT(*) FROM app.v_demandes_anonymisees", "autorisé")
    essai("novascore_lecture", lecture, "Lire les demandes complètes (données personnelles)",
          "SELECT COUNT(*) FROM app.demandes", "refusé")
    essai("novascore_lecture", lecture, "Lire les comptes utilisateurs", "SELECT COUNT(*) FROM app.utilisateurs", "refusé")
    essai("novascore_lecture", lecture, "Modifier les données sources",
          "UPDATE sources.credit_record SET status = 'X' WHERE id = 0", "refusé")
    essai("novascore_lecture", lecture, "Supprimer des données sources", "DELETE FROM sources.credit_record", "refusé")
    essai("novascore_app", "mauvais_mot_de_passe", "Se connecter avec un mauvais mot de passe", "SELECT 1", "refusé")

    with connecter(env, env.get("PG_SUPERUTILISATEUR", "postgres"), env["PG_SUPERUTILISATEUR_MDP"], base) as con:
        chiffrement = con.execute("SHOW password_encryption").fetchone()[0]

    conformes = sum(a == o for _, _, a, o in tests)
    lignes = [f"# Tests de sécurité par rôle — {datetime.now():%d/%m/%Y %H:%M}", "",
              "Chaque action est tentée en se connectant avec le rôle indiqué. Aucune modification n'est conservée "
              "(annulation systématique).", "",
              f"Chiffrement des mots de passe des rôles : **{chiffrement}**.", "",
              "| Rôle | Action tentée | Attendu | Obtenu | Conforme |", "|---|---|---|---|---|"]
    lignes += [f"| {r} | {a} | {att} | {obt} | {'oui' if att == obt else '**NON**'} |" for r, a, att, obt in tests]
    lignes += ["", f"**Résultat : {conformes} tests conformes sur {len(tests)}.**"]
    os.makedirs(DOSSIER_RAPPORTS, exist_ok=True)
    with open(os.path.join(DOSSIER_RAPPORTS, fichier), "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")
    for r, a, att, obt in tests:
        print(f"  {'OK ' if att == obt else 'ÉCHEC'} {r:18} {a} → {obt}")
    print(f"  {conformes}/{len(tests)} tests conformes")
    if conformes != len(tests):
        sys.exit("Des tests de sécurité ne sont pas conformes.")


# --------------------------------------------------------------------------- 7. dictionnaire
def exporter_dictionnaire(env):
    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], env.get("NOVASCORE_BASE", "novascore")) as con:
        objets = con.execute("""
            SELECT n.nspname, c.relname, CASE c.relkind WHEN 'r' THEN 'Table' ELSE 'Vue' END,
                   obj_description(c.oid, 'pg_class'), c.oid
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('sources', 'performance', 'app') AND c.relkind IN ('r', 'v')
            ORDER BY CASE n.nspname WHEN 'sources' THEN 1 WHEN 'app' THEN 2 ELSE 3 END, c.relkind, c.relname""").fetchall()
        lignes = [f"# Dictionnaire de données — base NovaScore ({datetime.now():%d/%m/%Y})", "",
                  "Généré automatiquement à partir des commentaires enregistrés dans la base (`COMMENT ON`).", ""]
        for schema, nom, genre, description, oid in objets:
            lignes += [f"## {genre} `{schema}.{nom}`", "", description or "", "",
                       "| Colonne | Type | Obligatoire | Description |", "|---|---|---|---|"]
            colonnes = con.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, col_description(a.attrelid, a.attnum)
                FROM pg_attribute a WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""", (oid,)).fetchall()
            for col, type_, obligatoire, desc in colonnes:
                lignes.append(f"| {col} | {type_} | {'oui' if obligatoire else 'non'} | {(desc or '').replace('|', '/')} |")
            contraintes = con.execute("""
                SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = %s AND contype IN ('p', 'f', 'c', 'u') ORDER BY contype, conname""", (oid,)).fetchall()
            if contraintes:
                lignes += ["", "Contraintes :", ""] + [f"- `{c}` : `{d}`" for c, d in contraintes]
            index = con.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s "
                                "ORDER BY indexname", (schema, nom)).fetchall()
            if index:
                lignes += ["", "Index :", ""] + [f"- `{i}`" for i, _ in index]
            lignes.append("")
    with open(os.path.join(DOSSIER_RAPPORTS, "dictionnaire_donnees.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))
    print(f"  {len(objets)} tables et vues documentées dans rapports/dictionnaire_donnees.md")


# --------------------------------------------------------------------------- 8. affichage
COMPTES_DEMO = [("admin", "admin", "admin"), ("analyste.demo", "Analyste2026", "analyste")]


def creer_comptes_demo(env):
    """Comptes de test de l'application, créés seulement si aucun compte n'existe encore.
    Même chiffrement que l'application : PBKDF2-SHA256, 200 000 itérations, sel aléatoire."""
    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], env.get("NOVASCORE_BASE", "novascore")) as con:
        if con.execute("SELECT COUNT(*) FROM app.utilisateurs").fetchone()[0]:
            print("  Des comptes existent déjà : aucun compte de démonstration ajouté.")
            return
        for identifiant, mot_de_passe, role in COMPTES_DEMO:
            sel = secrets.token_hex(16)
            empreinte = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), bytes.fromhex(sel), 200_000).hex()
            con.execute("INSERT INTO app.utilisateurs (identifiant, role, sel, empreinte, cree_par) VALUES (%s, %s, %s, %s, %s)",
                        (identifiant, role, sel, empreinte, "Installation"))
        con.commit()
    print("  Comptes de démonstration : " + ", ".join(f"{i} / {m} ({r})" for i, m, r in COMPTES_DEMO))


def afficher_tables(env):
    with connecter(env, "novascore_admin", env["NOVASCORE_ADMIN_MDP"], env.get("NOVASCORE_BASE", "novascore")) as con:
        objets = con.execute("""
            SELECT n.nspname || '.' || c.relname, CASE c.relkind WHEN 'r' THEN 'table' ELSE 'vue' END
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('sources', 'performance', 'app') AND c.relkind IN ('r', 'v') ORDER BY 1""").fetchall()
        print(f"  {'Objet':52} {'Type':6} {'Lignes':>11}")
        for nom, genre in objets:
            n = con.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.SQL(nom))).fetchone()[0]
            print(f"  {nom:52} {genre:6} {n:>11,}".replace(",", " "))


def verifier_connexion_superutilisateur(env):
    """Message clair si le mot de passe de postgres est refusé."""
    utilisateur = env.get("PG_SUPERUTILISATEUR", "postgres")
    try:
        connecter(env, utilisateur, env["PG_SUPERUTILISATEUR_MDP"], env.get("PG_BASE_MAINTENANCE", "postgres")).close()
    except psycopg.OperationalError as e:
        mdp = env["PG_SUPERUTILISATEUR_MDP"]
        sys.exit(f"\nConnexion impossible avec l'utilisateur « {utilisateur} ».\n"
                 f"Mot de passe lu dans .env : {len(mdp)} caractères, commence par « {mdp[:2]} », finit par « {mdp[-2:]} ».\n"
                 "Vérifiez qu'il est identique à celui défini dans pgAdmin (ALTER USER postgres PASSWORD '...').\n"
                 f"Détail : {str(e).splitlines()[0]}")


def main():
    reinitialiser = "--reinitialiser" in sys.argv
    en_ligne = "--en-ligne" in sys.argv
    env = lire_env(".env.en_ligne" if en_ligne else ".env")
    print(f"Installation de la base NovaScore sur {env.get('PG_HOTE', 'localhost')}:{env.get('PG_PORT', '5432')}"
          + (" (base en ligne)" if en_ligne else ""))
    verifier_connexion_superutilisateur(env)
    etape(1, "Rôles et base de données"); creer_base_et_roles(env, reinitialiser)
    etape(2, "Schémas, tables et vues"); executer_script(env, "02_tables.sql")
    if en_ligne:
        etape(3, "Chargement des CSV : non nécessaire en ligne (l'application n'utilise que le schéma app)")
    else:
        etape(3, "Contrôle qualité et chargement des CSV"); charger(env)
    etape(4, "Clés primaires et index"); executer_script(env, "03_index.sql")
    etape(5, "Droits par rôle, vue anonymisée et comptes de démonstration")
    executer_script(env, "04_securite.sql"); creer_comptes_demo(env)
    etape(6, "Tests de sécurité par rôle")
    tester_securite(env, "tests_securite_en_ligne.md" if en_ligne else "tests_securite.md")
    if en_ligne:
        etape(7, "Dictionnaire de données : déjà produit par l'installation locale")
    else:
        etape(7, "Dictionnaire de données"); exporter_dictionnaire(env)
    etape(8, "Tables de la base"); afficher_tables(env)
    if en_ligne:
        print("\nInstallation en ligne terminée. Changez le mot de passe du compte admin dès la première connexion.")
    else:
        print("\nInstallation terminée. Comparaison des performances : python mesurer_performances.py")


if __name__ == "__main__":
    main()
