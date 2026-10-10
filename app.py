"""NovaScore — application d'aide à la décision pour l'octroi de crédit.

Lancement : streamlit run app.py
Pré-requis :
  - la base PostgreSQL est installée (python installer_base.py) ;
  - le modèle est entraîné (Scoring_Credit.ipynb, dossier models/) ;
  - les paramètres de connexion sont renseignés : fichier .env en local,
    ou secrets Streamlit ([postgres] dans .streamlit/secrets.toml) une fois déployée.
L'application se connecte avec le rôle novascore_app (droits limités au schéma app).
Au premier lancement, le compte admin / admin est créé s'il n'existe encore aucun utilisateur.
"""
import hashlib
import html
import json
import math
import os
import re
import secrets
import warnings
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import psycopg
import requests
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
import shap
import streamlit as st
import streamlit.components.v1 as components

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- configuration
DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_MODELES = os.path.join(DOSSIER, "models")
FUSEAU = ZoneInfo("Europe/Paris")  # heure affichée dans l'application, quel que soit le serveur

VERT, VERT_FONCE, VERT_FOND = "#1E7A46", "#14532D", "#E6F3EC"
ROUGE, ROUGE_FONCE, ROUGE_FOND = "#C23B32", "#A3271F", "#FBEAEA"
BLEU, MARINE, ORANGE_FOND, ORANGE_TEXTE = "#1F5FA8", "#13294B", "#FEF3E2", "#8A4B08"
TEXTE, TEXTE_2, BORDURE = "#1B2430", "#4A5565", "#DDE3EA"

st.set_page_config(page_title="NovaScore", page_icon=":material/credit_score:", layout="wide")

# ---------------------------------------------------------------- libellés métier
VARIABLES = {
    "CODE_GENDER": "Genre", "AGE_ANNEES": "Âge", "NAME_EDUCATION_TYPE": "Niveau d'études",
    "NAME_FAMILY_STATUS": "Situation familiale", "CNT_CHILDREN": "Nombre d'enfants",
    "CNT_FAM_MEMBERS": "Membres du foyer", "NAME_INCOME_TYPE": "Type de revenu",
    "OCCUPATION_TYPE": "Métier", "SANS_EMPLOI": "Retraité(e) ou sans emploi",
    "ANCIENNETE_ANNEES": "Ancienneté dans l'emploi", "AMT_INCOME_TOTAL": "Revenu annuel",
    "NAME_HOUSING_TYPE": "Logement", "FLAG_OWN_REALTY": "Propriétaire d'un bien immobilier",
    "FLAG_OWN_CAR": "Possède une voiture", "FLAG_WORK_PHONE": "Téléphone professionnel",
    "FLAG_PHONE": "Téléphone fixe", "FLAG_EMAIL": "Adresse e-mail",
}
TRADUCTIONS = {
    "CODE_GENDER": {"F": "Femme", "M": "Homme"},
    "NAME_INCOME_TYPE": {"Working": "Salarié(e)", "Commercial associate": "Associé(e) commercial(e)",
                         "Pensioner": "Retraité(e)", "State servant": "Fonctionnaire", "Student": "Étudiant(e)"},
    "NAME_EDUCATION_TYPE": {"Secondary / secondary special": "Secondaire", "Higher education": "Enseignement supérieur",
                            "Incomplete higher": "Supérieur non terminé", "Lower secondary": "Premier cycle du secondaire",
                            "Academic degree": "Doctorat"},
    "NAME_FAMILY_STATUS": {"Married": "Marié(e)", "Single / not married": "Célibataire", "Civil marriage": "Union civile",
                           "Separated": "Séparé(e)", "Widow": "Veuf / veuve"},
    "NAME_HOUSING_TYPE": {"House / apartment": "Maison / appartement", "Rented apartment": "Appartement loué",
                          "With parents": "Chez les parents", "Municipal apartment": "Logement social",
                          "Office apartment": "Logement de fonction", "Co-op apartment": "Appartement en coopérative"},
    "OCCUPATION_TYPE": {"Inconnu": "Non renseigné", "Laborers": "Ouvrier(ère)", "Core staff": "Personnel administratif",
                        "Sales staff": "Commercial(e)", "Managers": "Cadre / manager", "Drivers": "Chauffeur",
                        "High skill tech staff": "Technicien(ne) qualifié(e)", "Accountants": "Comptable",
                        "Medicine staff": "Personnel médical", "Cooking staff": "Cuisinier(ère)",
                        "Security staff": "Agent de sécurité", "Cleaning staff": "Agent d'entretien",
                        "Private service staff": "Personnel de service", "Low-skill Laborers": "Ouvrier(ère) non qualifié(e)",
                        "Waiters/barmen staff": "Serveur(se) / barman", "Secretaries": "Secrétaire",
                        "Realty agents": "Agent immobilier", "HR staff": "Ressources humaines", "IT staff": "Informatique"},
}

# Champs du formulaire : clé, libellé, type, variable du modèle (listes déroulantes)
CHAMPS = [
    ("genre", "Genre", "liste", "CODE_GENDER"),
    ("age", "Âge (années)", "nombre", None),
    ("etudes", "Niveau d'études", "liste", "NAME_EDUCATION_TYPE"),
    ("situation", "Situation familiale", "liste", "NAME_FAMILY_STATUS"),
    ("enfants", "Enfants", "nombre", None),
    ("membres", "Membres du foyer", "nombre", None),
    ("typerevenu", "Type de revenu", "liste", "NAME_INCOME_TYPE"),
    ("metier", "Métier", "liste", "OCCUPATION_TYPE"),
    ("sansemploi", "Retraité(e) ou sans emploi", "ouinon", None),
    ("anciennete", "Ancienneté dans l'emploi (années)", "nombre", None),
    ("revenu", "Revenu annuel total", "nombre", None),
    ("logement", "Logement", "liste", "NAME_HOUSING_TYPE"),
    ("immo", "Propriétaire d'un bien immobilier", "ouinon", None),
    ("voiture", "Possède une voiture", "ouinon", None),
    ("telpro", "Téléphone professionnel", "ouinon", None),
    ("telfixe", "Téléphone fixe", "ouinon", None),
    ("email", "Adresse e-mail", "ouinon", None),
]
LIBELLES_CHAMPS = {c[0]: c[1].replace(" (années)", "") for c in CHAMPS}

CHAMPS_CSS = ["[data-baseweb='select'] > div", "[data-baseweb='input']", "[data-baseweb='textarea']",
              ".stSelectbox [role='group']", "[data-testid='stNumberInputContainer']",
              "[data-testid='stTextInputRootElement']", "[data-testid='stTextAreaRootElement']"]

ICONE_ERREUR = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
                'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle>'
                '<path d="M12 7.5v5.5M12 16.5v.01"></path></svg>')


# ---------------------------------------------------------------- style
def appliquer_style():
    champs_base = ", ".join(f".stApp .main {c}, .stApp [data-testid='stMain'] {c}" for c in CHAMPS_CSS)
    champs_focus = ", ".join(f".stApp [data-testid='stMain'] {c}:focus-within" for c in CHAMPS_CSS)
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp, button, input, textarea, select {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; }}
.stApp {{ background: #F3F5F8; color: {TEXTE}; }}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1280px; }}
h1, h2, h3 {{ color: {TEXTE}; font-weight: 600; }}
[data-testid="stSidebar"] {{ background: {MARINE}; }}
[data-testid="stSidebar"] * {{ color: #D5DDEA; }}
[data-testid="stSidebar"] .stButton button {{ justify-content: flex-start; background: transparent; border: 0; color: #D5DDEA; min-height: 44px; font-size: 15px; }}
[data-testid="stSidebar"] .stButton button:hover {{ background: #22406B; color: #FFFFFF; }}
[data-testid="stSidebar"] .stButton button[kind="primary"] {{ background: #2A4A78; color: #FFFFFF; font-weight: 500; }}
[data-testid="stSidebar"] .stButton button p {{ font-size: 15px; }}
.ns-marque {{ display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 600; color: #FFFFFF !important; padding: 4px 8px 18px; }}
.ns-connecte {{ font-size: 13px; color: #AEBBD0 !important; padding: 12px 8px 4px; border-top: 1px solid #2E4A72; }}
.ns-connecte strong {{ color: #FFFFFF !important; }}
.ns-sous-titre {{ color: {TEXTE_2}; font-size: 15px; margin-top: -10px; margin-bottom: 8px; }}
.ns-carte {{ background: #FFFFFF; border: 1px solid {BORDURE}; border-radius: 12px; padding: 20px; }}
.ns-kpi-libelle {{ font-size: 14px; font-weight: 500; color: {TEXTE_2}; display: flex; align-items: center; gap: 8px; }}
.ns-kpi-valeur {{ font-size: 32px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 4px 0; }}
.ns-kpi-detail {{ font-size: 13px; color: {TEXTE_2}; }}
.ns-pastille {{ width: 10px; height: 10px; border-radius: 5px; display: inline-block; }}
.ns-erreur {{ display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; color: {ROUGE_FONCE}; margin-top: -10px; margin-bottom: 8px; }}
.ns-recap {{ display: flex; gap: 12px; background: {ROUGE_FOND}; color: {ROUGE_FONCE}; border-radius: 8px; padding: 12px 14px; font-size: 14px; }}
.ns-recap span {{ color: {TEXTE}; }}
.ns-succes {{ display: flex; align-items: center; gap: 10px; background: {VERT_FOND}; color: {VERT_FONCE}; border-radius: 8px; padding: 12px 14px; font-size: 15px; font-weight: 500; margin-bottom: 12px; }}
.ns-info {{ background: #FFFFFF; border: 1px solid {BORDURE}; border-radius: 12px; padding: 28px; color: {TEXTE_2}; font-size: 15px; }}
.ns-legende {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: {TEXTE_2}; }}
.ns-legende span {{ display: inline-flex; align-items: center; gap: 6px; }}
.ns-tableau {{ width: 100%; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }}
.ns-tableau th, .ns-tableau td {{ padding: 11px 10px; border-top: 1px solid #EEF1F5; }}
.ns-tableau thead th {{ border-top: 0; color: {TEXTE_2}; font-weight: 500; }}
.ns-facteur {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; font-size: 14px; }}
.ns-facteur-ligne {{ display: flex; justify-content: space-between; gap: 12px; }}
.ns-facteur-barre {{ display: flex; align-items: center; gap: 8px; height: 20px; background: #F1F3F6; border-radius: 4px; }}
.ns-facteur-barre > div {{ height: 100%; border-radius: 4px; display: flex; align-items: center; justify-content: flex-end; box-sizing: border-box; padding-right: 8px; color: #FFFFFF; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ns-facteur-barre > span {{ font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }}
div[data-testid="stForm"] {{ background: #FFFFFF; border: 1px solid {BORDURE}; border-radius: 12px; }}
{champs_base} {{ background: #FFFFFF !important; border: 1px solid #C5CED9 !important; border-radius: 8px !important; }}
{champs_focus} {{ border-color: {BLEU} !important; box-shadow: 0 0 0 1px {BLEU} !important; }}
.stApp [data-testid^='stBaseButton-primary'] {{ background: {BLEU} !important; border-color: {BLEU} !important; color: #FFFFFF !important; }}
.stApp [data-testid^='stBaseButton-primary']:hover {{ background: #174C88 !important; border-color: #174C88 !important; }}
.stApp [data-testid^='stBaseButton-secondary'] {{ border-color: #C5CED9; }}
.stApp [data-testid^='stBaseButton-secondary']:hover {{ border-color: {BLEU}; color: {BLEU}; }}
[data-testid='stRadioOption'][data-selected='true'] > div > div:first-child {{ background-color: {BLEU} !important; border-color: {BLEU} !important; }}
[data-testid="stSidebar"] [data-testid^="stBaseButton"] {{ justify-content: flex-start !important; background: transparent !important; border: 0 !important; color: #D5DDEA !important; padding-left: 12px; }}
[data-testid="stSidebar"] [data-testid^="stBaseButton"] > div {{ justify-content: flex-start !important; width: 100%; }}
[data-testid="stSidebar"] [data-testid^="stBaseButton"]:hover {{ background: #22406B !important; color: #FFFFFF !important; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {{ background: #2A4A78 !important; color: #FFFFFF !important; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] p {{ font-weight: 500; }}
.ns-carte {{ min-height: 172px; box-sizing: border-box; }}
.ns-recap svg, .ns-erreur svg {{ flex: 0 0 auto; }}
.ns-recap svg {{ width: 18px; height: 18px; margin-top: 2px; }}
</style>""", unsafe_allow_html=True)


def style_erreurs(cles):
    """Encadre en rouge les champs en erreur (repérés par leur clé)."""
    if not cles:
        return
    regles = []
    for cle in cles:
        champs = ", ".join(f".stApp [data-testid='stMain'] .st-key-{cle} {c}, .stApp .main .st-key-{cle} {c}" for c in CHAMPS_CSS)
        regles.append(f"{champs} {{ border-color: {ROUGE} !important; box-shadow: 0 0 0 1px {ROUGE} !important; "
                      f"background: #FFF6F6 !important; }}")
        regles.append(f".stApp .st-key-{cle} [role='radiogroup'] {{ background: #FFF6F6; box-shadow: 0 0 0 1px {ROUGE}; "
                      f"border-radius: 8px; padding: 6px 10px; }}")
    st.markdown("<style>" + "\n".join(regles) + "</style>", unsafe_allow_html=True)


def message_erreur(texte):
    st.markdown(f'<div class="ns-erreur" role="alert">{ICONE_ERREUR}{texte}</div>', unsafe_allow_html=True)


def message_succes(texte):
    st.markdown(f'<div class="ns-succes" role="status">'
                f'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="{VERT}" stroke-width="2" '
                f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle>'
                f'<path d="M8 12.5l2.8 2.8L16 9.5"></path></svg>{texte}</div>', unsafe_allow_html=True)


def nombre_fr(x):
    return f"{x:,.0f}".replace(",", " ")


def pct_fr(x):
    return f"{x * 100:.1f}".replace(".", ",") + " %"


# ---------------------------------------------------------------- base de données
def lire_env():
    """Lit le fichier .env (lignes CLE=valeur), s'il existe."""
    valeurs = {}
    chemin = os.path.join(DOSSIER, ".env")
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8-sig") as f:
            for ligne in f:
                ligne = ligne.strip()
                if ligne and not ligne.startswith("#") and "=" in ligne:
                    cle, valeur = ligne.split("=", 1)
                    valeurs[cle.strip()] = valeur.strip().strip('"').strip("'")
    return valeurs


def parametres_connexion():
    """Paramètres PostgreSQL : secrets Streamlit (déploiement) en priorité, sinon fichier .env (poste local)."""
    try:
        if "postgres" in st.secrets:
            p = st.secrets["postgres"]
            return dict(host=p["hote"], port=str(p.get("port", 5432)), dbname=p["base"], user=p["utilisateur"],
                        password=p["mot_de_passe"], sslmode=p.get("sslmode", "require"))
    except Exception:  # aucun fichier de secrets : on passe au fichier .env
        pass
    env = lire_env()
    return dict(host=env.get("PG_HOTE", "localhost"), port=env.get("PG_PORT", "5432"),
                dbname=env.get("NOVASCORE_BASE", "novascore"), user="novascore_app",
                password=env.get("NOVASCORE_APP_MDP", ""), sslmode=env.get("PG_SSLMODE", "prefer"))


@st.cache_resource(show_spinner=False)
def reserve_connexions():
    """Réserve de connexions partagée par tous les utilisateurs de l'application.
    Ouvrir une connexion coûte cher (surtout vers une base en ligne) : on les réutilise."""
    reserve = ConnectionPool(
        kwargs={**parametres_connexion(), "row_factory": dict_row, "connect_timeout": 10,
                "options": "-c timezone=Europe/Paris"},
        min_size=1, max_size=5, timeout=15, check=ConnectionPool.check_connection, open=False)
    try:
        reserve.open(wait=True, timeout=10)
    except Exception:
        reserve.close()  # pas de tentatives de reconnexion en arrière-plan : on réessaiera au prochain affichage
        raise
    return reserve


def bd():
    """Connexion à utiliser dans un bloc « with » : validation (commit) à la fin du bloc,
    annulation (rollback) en cas d'erreur, puis retour de la connexion dans la réserve."""
    return reserve_connexions().connection()


def hacher(motdepasse, sel=None):
    sel = sel or secrets.token_hex(16)
    empreinte = hashlib.pbkdf2_hmac("sha256", motdepasse.encode("utf-8"), bytes.fromhex(sel), 200_000).hex()
    return sel, empreinte


def verifier_motdepasse(motdepasse, sel, empreinte):
    return secrets.compare_digest(hacher(motdepasse, sel)[1], empreinte)


def maintenant():
    return datetime.now(FUSEAU)


def initialiser_base():
    """Les tables sont créées par installer_base.py (sql/02_tables.sql) : l'application n'a pas le droit d'en créer.
    Ici, on crée seulement le premier compte administrateur si aucun compte n'existe."""
    with bd() as con:
        if con.execute("SELECT COUNT(*) AS n FROM app.utilisateurs").fetchone()["n"] == 0:
            sel, emp = hacher("admin")
            con.execute("INSERT INTO app.utilisateurs (identifiant, role, sel, empreinte, cree_le, cree_par) "
                        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (identifiant) DO NOTHING",
                        ("admin", "admin", sel, emp, maintenant(), "Système"))


def page_base_indisponible(erreur):
    st.markdown('<div class="ns-info" role="alert"><strong style="color:#1B2430;font-size:20px">'
                'Service momentanément indisponible</strong><br>La base de données ne répond pas. '
                'Réessayez dans quelques instants ; si le problème persiste, contactez l\'administrateur.</div>',
                unsafe_allow_html=True)
    with st.expander("Détail technique (administrateur)"):
        st.code(f"{type(erreur).__name__} : {str(erreur).splitlines()[0] if str(erreur) else ''}")


def utilisateur_par_id(uid):
    with bd() as con:
        return con.execute("SELECT * FROM app.utilisateurs WHERE id = %s", (uid,)).fetchone()


def lister_utilisateurs():
    with bd() as con:
        return con.execute("SELECT * FROM app.utilisateurs ORDER BY id").fetchall()


def charger_demandes():
    # Conversions pour pandas : nombres décimaux → réels, JSONB → texte JSON, booléen → 0/1
    with bd() as con:
        lignes = con.execute(
            "SELECT id, reference, date, analyste, donnees::text AS donnees, age::float8 AS age, revenu::float8 AS revenu, "
            "score::float8 AS score, recommandation, decision, commentaire, facteurs::text AS facteurs, "
            "corrigee::int AS corrigee FROM app.demandes ORDER BY date DESC, id DESC").fetchall()
    colonnes = ["id", "reference", "date", "analyste", "donnees", "age", "revenu", "score", "recommandation",
                "decision", "commentaire", "facteurs", "corrigee"]
    df = pd.DataFrame(lignes, columns=colonnes)
    # Dates enregistrées avec leur fuseau : affichées à l'heure de Paris
    df["date_dt"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(FUSEAU).dt.tz_localize(None)
    return df


def filtrer_periode(df, periode):
    if periode == "tout" or df.empty:
        return df
    jours = 7 if periode == "7" else 30
    debut = pd.Timestamp(maintenant().date()) - pd.Timedelta(days=jours - 1)
    return df[df["date_dt"] >= debut]


# ---------------------------------------------------------------- modèle
@st.cache_resource
def charger_modele():
    modele = joblib.load(os.path.join(DOSSIER_MODELES, "modele_scoring_credit.joblib"))
    seuil = float(joblib.load(os.path.join(DOSSIER_MODELES, "seuil_decision.joblib")))
    colonnes = joblib.load(os.path.join(DOSSIER_MODELES, "colonnes_modele.joblib"))
    preparation = modele.named_steps["preparation"]
    colonnes_num = list(preparation.transformers_[0][2])
    colonnes_cat = list(preparation.transformers_[1][2])
    encodeur = preparation.named_transformers_["categorielle"].named_steps["encodage"]
    categories = {c: [str(v) for v in cats] for c, cats in zip(colonnes_cat, encodeur.categories_)}
    algorithme = modele.named_steps["modele"]
    explainer = None if hasattr(algorithme, "coef_") else shap.TreeExplainer(algorithme)
    return {"modele": modele, "seuil": seuil, "colonnes": colonnes, "num": colonnes_num, "cat": colonnes_cat,
            "categories": categories, "explainer": explainer}


def traduire(variable, valeur):
    return TRADUCTIONS.get(variable, {}).get(valeur, valeur)


def options_liste(variable):
    valeurs = charger_modele()["categories"][variable]
    valeurs = sorted(valeurs, key=lambda v: (v != "Inconnu", traduire(variable, v)))
    return valeurs


def valeur_affichee(variable, x):
    v = x[variable]
    if variable == "AGE_ANNEES":
        return f"{int(round(v))} ans"
    if variable == "ANCIENNETE_ANNEES":
        return "sans objet" if x["SANS_EMPLOI"] == 1 else f"{v:g} an" + ("s" if v >= 2 else "")
    if variable == "AMT_INCOME_TOTAL":
        return nombre_fr(v)
    if variable in ("CNT_CHILDREN", "CNT_FAM_MEMBERS"):
        return str(int(v))
    if variable in ("FLAG_OWN_REALTY", "FLAG_OWN_CAR"):
        return "Oui" if v == "Y" else "Non"
    if variable in ("FLAG_WORK_PHONE", "FLAG_PHONE", "FLAG_EMAIL", "SANS_EMPLOI"):
        return "Oui" if int(v) == 1 else "Non"
    return traduire(variable, v)


def evaluer_modele(x):
    """x : dictionnaire des variables du modèle → score, recommandation et facteurs SHAP."""
    m = charger_modele()
    X = pd.DataFrame([x])[m["colonnes"]]
    proba = float(m["modele"].predict_proba(X)[0, 1])
    preparation = m["modele"].named_steps["preparation"]
    Xp = preparation.transform(X)
    Xp = Xp.toarray() if hasattr(Xp, "toarray") else np.asarray(Xp)
    if m["explainer"] is None:
        valeurs = m["modele"].named_steps["modele"].coef_ * Xp
    else:
        valeurs = m["explainer"].shap_values(Xp)
        if isinstance(valeurs, list):
            valeurs = valeurs[1]
        if np.ndim(valeurs) == 3:
            valeurs = valeurs[:, :, 1]
    origine = list(m["num"])
    for c in m["cat"]:
        origine += [c] * len(m["categories"][c])
    contributions = pd.Series(np.ravel(valeurs)).groupby(origine).sum()
    facteurs = [{"variable": var, "libelle": f"{VARIABLES[var]} : {valeur_affichee(var, x)}",
                 "contribution": float(c)} for var, c in contributions.items()]
    facteurs.sort(key=lambda f: abs(f["contribution"]), reverse=True)
    return {"proba": proba, "score": proba * 100, "seuil": m["seuil"],
            "recommandation": "refus" if proba >= m["seuil"] else "accord", "facteurs": facteurs}


def valeur_shap_fr(c):
    """Contribution SHAP au format français, avec son signe : +2,10 / −0,25."""
    return ("+" if c > 0 else "−") + f"{abs(c):.2f}".replace(".", ",")


def niveau_impact(c):
    """Niveau d'impact à seuils fixes, identiques pour tous les dossiers."""
    c = round(abs(c), 2)  # niveau cohérent avec la valeur affichée (2 décimales)
    return "Très fort" if c >= 1 else "Fort" if c >= 0.5 else "Modéré" if c >= 0.2 else "Faible"


def afficher_facteurs(facteurs, nb=4):
    hausses = [f for f in facteurs if f["contribution"] > 0][:nb]
    baisses = [f for f in facteurs if f["contribution"] < 0][:nb]
    maximum = max([abs(f["contribution"]) for f in hausses + baisses] or [1])  # même échelle pour les deux colonnes

    def bloc(liste, couleur, couleur_texte, titre):
        html_bloc = f'<div style="font-size:14px;font-weight:600;color:{couleur_texte};margin-bottom:10px">{titre}</div>'
        if not liste:
            html_bloc += f'<div style="font-size:14px;color:{TEXTE_2}">Aucun facteur notable.</div>'
        for f in liste:
            r = abs(f["contribution"]) / maximum
            valeur = valeur_shap_fr(f["contribution"])
            dedans = r >= 0.2  # la valeur tient dans la barre ; sinon elle est écrite juste après
            html_bloc += (f'<div class="ns-facteur"><div class="ns-facteur-ligne"><span>{f["libelle"]}</span>'
                          f'<span style="color:{TEXTE_2}">{niveau_impact(f["contribution"])}</span></div>'
                          f'<div class="ns-facteur-barre" role="img" aria-label="{f["libelle"]} : contribution {valeur}">'
                          f'<div style="width:{max(r * 100, 3):.0f}%;background:{couleur}">{valeur if dedans else ""}</div>'
                          f'{"" if dedans else f"<span style=color:{couleur_texte}>{valeur}</span>"}</div></div>')
        return html_bloc

    c1, c2 = st.columns(2, gap="large")
    c1.markdown(bloc(hausses, ROUGE, ROUGE_FONCE, "Ce qui augmente le risque"), unsafe_allow_html=True)
    c2.markdown(bloc(baisses, BLEU, BLEU, "Ce qui diminue le risque"), unsafe_allow_html=True)


# ---------------------------------------------------------------- assistant d'explication (LLM Claude)
# Rôle : traduire le résultat du modèle en explication compréhensible par l'analyste, signaler les points
# à vérifier et proposer un commentaire. L'assistant ne décide jamais : il ne reçoit que le score, le seuil
# et les facteurs SHAP (libellés métier), sans aucune donnée d'identification. Sa réponse est contrôlée ;
# si elle est refusée ou si le service est indisponible, une explication automatique (règles fixes) la remplace.
ASSISTANT_URL = "https://api.anthropic.com/v1/messages"
ASSISTANT_MODELE = "claude-haiku-5-5"
VARIABLES_SENSIBLES = {"CODE_GENDER": "le genre", "AGE_ANNEES": "l'âge", "NAME_FAMILY_STATUS": "la situation familiale"}
VERIFICATIONS = {
    "AMT_INCOME_TOTAL": "justificatifs de revenus et existence d'autres revenus",
    "ANCIENNETE_ANNEES": "date d'embauche et nature du contrat",
    "NAME_INCOME_TYPE": "source et régularité des revenus",
    "OCCUPATION_TYPE": "métier exercé et stabilité de l'emploi",
    "SANS_EMPLOI": "situation professionnelle actuelle",
    "NAME_HOUSING_TYPE": "situation de logement et charges associées",
    "FLAG_OWN_REALTY": "justificatif de propriété",
    "FLAG_OWN_CAR": "information déclarée sur le véhicule",
    "CNT_CHILDREN": "composition du foyer et charges",
    "CNT_FAM_MEMBERS": "composition du foyer et charges",
    "NAME_EDUCATION_TYPE": "exactitude de l'information saisie",
}
MOTS_INTERDITS = re.compile(
    r"\b(accord\w*|accept\w*|refus\w*|rejet\w*|octroi\w*|octroy\w*|solvab\w*|insolvab\w*|caus\w*)\b"
    r"|(fera|feront|va faire|vont faire) défaut", re.IGNORECASE)
CONSIGNES_ASSISTANT = """Tu es l'assistant d'explication de NovaScore, un outil d'aide à la décision pour des analystes crédit.
Un modèle a calculé un score de risque de défaut grave (0 à 100) pour un dossier. Tu reçois ce score, le seuil de recommandation
et les facteurs qui expliquent le score (valeurs SHAP : positif = augmente le risque, négatif = le diminue).

Ton rôle : aider l'analyste à comprendre le score, à identifier les points à vérifier et à documenter son analyse.
Règles strictes :
1. Tu ne décides rien : ne dis jamais si le client est solvable, ne recommande aucune décision, n'emploie jamais les mots
   accord, accorder, accepter, refus, refuser, rejeter, octroyer, solvable.
2. Les facteurs sont des associations repérées par le modèle, pas des causes : n'écris jamais « cause », « parce que le client »
   ou « fera défaut ».
3. N'écris aucun chiffre qui ne figure pas dans les données fournies ; recopie les nombres exactement comme ils sont écrits
   (par exemple « +2,10 », « 108 000 »). Écris les autres quantités en toutes lettres.
4. Français simple, sans jargon technique (pas de « SHAP », « log-odds », « modèle de machine learning »).
5. Si l'âge, le genre ou la situation familiale figurent parmi les facteurs, rappelle qu'ils ne peuvent pas motiver une décision.

Réponds uniquement par un objet JSON, sans texte autour :
{"comprendre": "deux ou trois phrases", "points_a_verifier": ["deux à quatre points courts"],
 "proposition_commentaire": "une ou deux phrases factuelles que l'analyste pourra reprendre"}"""


def parametres_assistant():
    """Clé d'API : secrets Streamlit (en ligne), puis variables d'environnement, puis fichier .env (poste local)."""
    try:
        if "assistant" in st.secrets:
            a = st.secrets["assistant"]
            return {"cle": a.get("cle_api", ""), "modele": a.get("modele", ASSISTANT_MODELE), "url": a.get("url", ASSISTANT_URL)}
    except Exception:
        pass
    env = {**lire_env(), **os.environ}
    return {"cle": env.get("ANTHROPIC_API_KEY", ""), "modele": env.get("ASSISTANT_MODELE", ASSISTANT_MODELE),
            "url": env.get("ASSISTANT_URL", ASSISTANT_URL)}


def separer_libelle(libelle):
    """« Revenu annuel : 108 000 » → (« revenu annuel », « 108 000 »)."""
    nom, _, valeur = libelle.partition(" : ")
    return nom[:1].lower() + nom[1:], valeur


def donnees_assistant(r):
    """Ce qui est transmis à l'assistant : score, seuil et facteurs, sans identifiant ni donnée brute du formulaire."""
    facteurs = [f for f in r["facteurs"] if abs(f["contribution"]) >= 0.05][:8]
    return {
        "score_sur_100": f"{r['score']:.0f}",
        "seuil_de_recommandation": f"{r['seuil'] * 100:.0f}",
        "position": "au-dessus du seuil" if r["recommandation"] == "refus" else "en dessous du seuil",
        "facteurs": [{"libelle": f["libelle"], "valeur": valeur_shap_fr(f["contribution"]),
                      "effet": "augmente le risque" if f["contribution"] > 0 else "diminue le risque",
                      "impact": niveau_impact(f["contribution"])} for f in facteurs],
    }


def nombres(texte):
    """Nombres présents dans un texte, normalisés : « 108 000 » → « 108000 », « +2,10 » → « 2,10 »."""
    return {re.sub(r"[\s\u202f\u00a0]", "", n) for n in re.findall(r"\d+(?:[\s\u202f\u00a0]\d{3})*(?:,\d+)?", texte)}


def controler_reponse(reponse, donnees):
    """Refuse une réponse incomplète, qui cite un chiffre absent des données ou qui emploie une formule de décision."""
    if not isinstance(reponse, dict):
        return False
    comprendre, points, proposition = (reponse.get("comprendre"), reponse.get("points_a_verifier"),
                                       reponse.get("proposition_commentaire"))
    if not (isinstance(comprendre, str) and isinstance(proposition, str) and isinstance(points, list)
            and 1 <= len(points) <= 5 and all(isinstance(p, str) and p.strip() for p in points)
            and comprendre.strip() and proposition.strip()):
        return False
    texte = " ".join([comprendre, proposition, *points])
    if MOTS_INTERDITS.search(texte):
        return False
    autorises = nombres(json.dumps(donnees, ensure_ascii=False))
    return nombres(texte) <= autorises


def explication_automatique(r):
    """Explication rédigée par des règles fixes, à partir des mêmes données (secours et fonctionnement sans clé)."""
    d = donnees_assistant(r)
    hausses = [f for f in r["facteurs"] if f["contribution"] > 0][:3]
    baisses = [f for f in r["facteurs"] if f["contribution"] < 0][:3]
    decrire = lambda f: "{} ({}, {})".format(*separer_libelle(f["libelle"]), valeur_shap_fr(f["contribution"]))
    phrases = [f"Le score de {d['score_sur_100']} / 100 "
               f"{'dépasse le' if r['recommandation'] == 'refus' else 'est inférieur au'} seuil de recommandation "
               f"({d['seuil_de_recommandation']})."]
    if hausses:
        phrases.append("Facteurs qui l'augmentent le plus : " + ", ".join(decrire(f) for f in hausses) + ".")
    if baisses:
        favorables = "Facteurs qui le diminuent : " + ", ".join(decrire(f) for f in baisses)
        if r["recommandation"] == "refus":
            somme_h = sum(f["contribution"] for f in hausses)
            somme_b = -sum(f["contribution"] for f in baisses)
            favorables += " : ils pèsent peu face aux précédents" if somme_b < 0.5 * somme_h else " : ils compensent en partie"
        phrases.append(favorables + ".")
    if r["recommandation"] != "refus" and hausses:
        phrases.append("Ensemble, les facteurs qui augmentent le risque restent insuffisants pour atteindre le seuil.")

    # Facteur décisif : sans lui, le dossier passerait de l'autre côté du seuil
    logit = lambda p: math.log(max(p, 1e-9) / max(1 - p, 1e-9))
    l_dossier, l_seuil = logit(r["proba"]), logit(r["seuil"])
    points = []
    for f in [f for f in r["facteurs"] if f["variable"] not in VARIABLES_SENSIBLES and abs(f["contribution"]) >= 0.2][:2]:
        nom, valeur = separer_libelle(f["libelle"])
        point = f"{nom[:1].upper() + nom[1:]} ({valeur}) : {VERIFICATIONS.get(f['variable'], 'exactitude de l’information saisie')}."
        if (l_dossier >= l_seuil) != (l_dossier - f["contribution"] >= l_seuil):
            point += " Ce facteur fait à lui seul basculer la recommandation."
        points.append(point)
    sensibles = [VARIABLES_SENSIBLES[f["variable"]] for f in d_facteurs_complets(r) if f["variable"] in VARIABLES_SENSIBLES]
    if sensibles:
        liste = ", ".join(sensibles[:-1]) + (" et " if len(sensibles) > 1 else "") + sensibles[-1]
        points.append(f"{liste[:1].upper() + liste[1:]} : {'ils influencent' if len(sensibles) > 1 else 'il influence'} "
                      f"le score mais ne {'peuvent' if len(sensibles) > 1 else 'peut'} pas motiver une décision.")
    if not points:
        points.append("Exactitude des informations saisies.")
    principaux = [f for f in r["facteurs"] if f["variable"] not in VARIABLES_SENSIBLES][:2]
    proposition = (f"Score de {d['score_sur_100']} / 100, principalement expliqué par : "
                   + ", ".join("{} ({})".format(*separer_libelle(f["libelle"])) for f in principaux)
                   + ". Éléments à confirmer sur pièces avant la décision finale.")
    return {"comprendre": " ".join(phrases), "points_a_verifier": points, "proposition_commentaire": proposition}


def d_facteurs_complets(r):
    """Facteurs transmis à l'assistant (mêmes règles de sélection que donnees_assistant)."""
    return [f for f in r["facteurs"] if abs(f["contribution"]) >= 0.05][:8]


def interroger_assistant(r):
    """Explication par Claude si une clé est configurée et si la réponse passe les contrôles ; sinon explication automatique.
    Renvoie (explication, source, modèle) avec source « ia » ou « automatique »."""
    p = parametres_assistant()
    if not p["cle"]:
        return explication_automatique(r), "automatique", None
    donnees = donnees_assistant(r)
    try:
        reponse = requests.post(
            p["url"], timeout=20,
            headers={"x-api-key": p["cle"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": p["modele"], "max_tokens": 700, "temperature": 0, "system": CONSIGNES_ASSISTANT,
                  "messages": [{"role": "user", "content": json.dumps(donnees, ensure_ascii=False)}]})
        reponse.raise_for_status()
        texte = "".join(b.get("text", "") for b in reponse.json().get("content", []) if b.get("type") == "text")
        explication = json.loads(texte[texte.index("{"):texte.rindex("}") + 1])
        if controler_reponse(explication, donnees):
            return ({"comprendre": explication["comprendre"].strip(),
                     "points_a_verifier": [x.strip() for x in explication["points_a_verifier"]],
                     "proposition_commentaire": explication["proposition_commentaire"].strip()}, "ia", p["modele"])
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        pass  # service indisponible ou réponse illisible : explication automatique
    return explication_automatique(r), "automatique", None


def utiliser_proposition():
    a = st.session_state.resultat["assistant"]
    v = st.session_state.get("version_form", 0)
    st.session_state[f"commentaire_{v}"] = a["explication"]["proposition_commentaire"]
    st.session_state.proposition_utilisee = True


def afficher_assistant(r):
    if "assistant" not in r:
        with st.spinner("L'assistant prépare l'explication…"):
            explication, source, modele = interroger_assistant(r)
        r["assistant"] = {"explication": explication, "source": source, "modele": modele}
    a = r["assistant"]
    e = a["explication"]
    badge = (f'<span style="font-size:12px;font-weight:600;color:{ORANGE_TEXTE};background:{ORANGE_FOND};border-radius:12px;'
             f'padding:4px 10px">Rédigé avec l\'aide de l\'IA · à vérifier</span>' if a["source"] == "ia" else
             f'<span style="font-size:12px;font-weight:600;color:{TEXTE};background:#F1F3F6;border-radius:12px;'
             f'padding:4px 10px">Explication automatique</span>')
    points = "".join(f"<li>{html.escape(p)}</li>" for p in e["points_a_verifier"])
    st.markdown(f"""
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:6px">
  <h3 style="margin:0;font-size:1.5rem;display:flex;align-items:center;gap:10px">
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="{BLEU}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z"></path><path d="M19 15l.8 1.9 1.9.8-1.9.8L19 20.4l-.8-1.9-1.9-.8 1.9-.8z"></path></svg>
  Explication de l'assistant</h3>{badge}</div>
<div style="font-size:14px;font-weight:600;color:{MARINE};margin-top:10px">Comprendre le score</div>
<p style="font-size:15px;line-height:1.6;margin:4px 0 12px">{html.escape(e["comprendre"])}</p>
<div style="font-size:14px;font-weight:600;color:{MARINE}">Points à vérifier</div>
<ul style="font-size:15px;line-height:1.6;margin:4px 0 12px;padding-left:20px">{points}</ul>
<div style="font-size:14px;font-weight:600;color:{MARINE}">Proposition de commentaire</div>
<div style="border:1px solid {BORDURE};border-left:4px solid {BLEU};border-radius:8px;padding:12px 14px;font-size:15px;line-height:1.6;background:#F7F9FC;margin:6px 0 10px">{html.escape(e["proposition_commentaire"])}</div>
""", unsafe_allow_html=True)
    if st.session_state.get("etape") == "resultat":
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        c1.button("Utiliser comme commentaire", key="btn_proposition", width="stretch", on_click=utiliser_proposition)
        if st.session_state.get("proposition_utilisee"):
            c2.markdown(f'<span role="status" style="font-size:14px;color:{VERT};font-weight:500">'
                        'Ajoutée au commentaire : vous pouvez la modifier avant d\'enregistrer.</span>', unsafe_allow_html=True)


# ---------------------------------------------------------------- session et navigation
def etat(cle, defaut):
    if cle not in st.session_state:
        st.session_state[cle] = defaut
    return st.session_state[cle]


def utilisateur_courant():
    uid = st.session_state.get("uid")
    if uid is None:
        return None
    u = utilisateur_par_id(uid)
    if u is None:  # compte supprimé entre-temps
        st.session_state.clear()
        return None
    return dict(u)


def aller(page):
    st.session_state.page = page
    if page == "nouvelle":
        reinitialiser_demande()


def deconnecter():
    st.session_state.clear()


# ---------------------------------------------------------------- page : connexion
def page_connexion():
    st.markdown("<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'], "
                "[data-testid='stExpandSidebarButton'] { display: none !important; }</style>", unsafe_allow_html=True)
    gauche, droite = st.columns([1, 1], gap="large")
    with gauche:
        st.markdown(f"""
<div style="background:{MARINE};color:#FFFFFF;border-radius:16px;padding:48px 40px;min-height:460px;display:flex;flex-direction:column;justify-content:space-between;gap:40px">
  <div style="display:flex;align-items:center;gap:12px;font-size:20px;font-weight:600">
    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="5" width="19" height="14" rx="2.5"></rect><path d="M2.5 9.5h19"></path><path d="M6.5 15h4"></path></svg>NovaScore</div>
  <div><div style="font-size:34px;line-height:1.2;font-weight:600;margin-bottom:14px">Aide à la décision pour l'octroi de crédit</div>
  <div style="font-size:17px;line-height:1.6;color:#C9D4E4">Évaluez une demande en quelques secondes, comprenez les facteurs de risque et enregistrez votre décision.</div></div>
  <div></div>
</div>""", unsafe_allow_html=True)
    with droite:
        st.markdown("<div style='height:40px'></div>", unsafe_allow_html=True)
        st.markdown("## Connexion")
        st.markdown('<div class="ns-sous-titre">Identifiez-vous pour accéder à l\'application.</div>', unsafe_allow_html=True)
        with st.form("connexion", border=True):
            identifiant = st.text_input("Identifiant", placeholder="Votre identifiant", key="login_id")
            motdepasse = st.text_input("Mot de passe", type="password", placeholder="Votre mot de passe", key="login_mdp")
            envoye = st.form_submit_button("Se connecter", type="primary", width="stretch")
        if envoye:
            with bd() as con:
                u = con.execute("SELECT * FROM app.utilisateurs WHERE identifiant = %s", (identifiant.strip().lower(),)).fetchone()
            if u and verifier_motdepasse(motdepasse, u["sel"], u["empreinte"]):
                st.session_state.clear()
                st.session_state.uid = u["id"]
                st.session_state.page = "tableau"
                st.rerun()
            else:
                st.markdown(f'<div class="ns-recap" role="alert">{ICONE_ERREUR}Identifiant ou mot de passe incorrect.</div>',
                            unsafe_allow_html=True)


# ---------------------------------------------------------------- menu latéral
def menu(utilisateur):
    page = etat("page", "tableau")
    with st.sidebar:
        st.markdown('<div class="ns-marque"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" '
                    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="5" '
                    'width="19" height="14" rx="2.5"></rect><path d="M2.5 9.5h19"></path><path d="M6.5 15h4"></path></svg>'
                    'NovaScore</div>', unsafe_allow_html=True)
        entrees = [("tableau", "Tableau de bord", ":material/bar_chart:"),
                   ("nouvelle", "Nouvelle demande", ":material/add:"),
                   ("historique", "Historique", ":material/history:")]
        if utilisateur["role"] == "admin":
            entrees.append(("utilisateurs", "Utilisateurs", ":material/group:"))
        for cle, libelle, icone in entrees:
            st.button(libelle, key=f"nav_{cle}", icon=icone, width="stretch",
                      type="primary" if page == cle else "secondary", on_click=aller, args=(cle,))
        st.markdown(f'<div class="ns-connecte" style="margin-top:32px">Connecté en tant que '
                    f'<strong>{utilisateur["identifiant"]}</strong></div>', unsafe_allow_html=True)
        st.button("Déconnexion", key="nav_deconnexion", icon=":material/logout:", width="stretch", on_click=deconnecter)


# ---------------------------------------------------------------- page : nouvelle demande
def reinitialiser_demande():
    st.session_state.version_form = st.session_state.get("version_form", 0) + 1
    for cle in ("etape", "tentative", "resultat", "ref_enregistree", "decision_enregistree"):
        st.session_state.pop(cle, None)


def cle_champ(nom):
    return f"{nom}_{st.session_state.get('version_form', 0)}"


def valeur_champ(nom):
    return st.session_state.get(cle_champ(nom))


def champs_manquants():
    manquants = []
    for nom, _, _, _ in CHAMPS:
        if nom == "anciennete" and valeur_champ("sansemploi") == "Oui":
            continue
        if valeur_champ(nom) is None:
            manquants.append(nom)
    return manquants


def construire_variables():
    oui = lambda nom: valeur_champ(nom) == "Oui"
    sans_emploi = oui("sansemploi")
    return {
        "CODE_GENDER": valeur_champ("genre"), "FLAG_OWN_CAR": "Y" if oui("voiture") else "N",
        "FLAG_OWN_REALTY": "Y" if oui("immo") else "N", "CNT_CHILDREN": int(valeur_champ("enfants")),
        "AMT_INCOME_TOTAL": float(valeur_champ("revenu")), "NAME_INCOME_TYPE": valeur_champ("typerevenu"),
        "NAME_EDUCATION_TYPE": valeur_champ("etudes"), "NAME_FAMILY_STATUS": valeur_champ("situation"),
        "NAME_HOUSING_TYPE": valeur_champ("logement"), "FLAG_WORK_PHONE": int(oui("telpro")),
        "FLAG_PHONE": int(oui("telfixe")), "FLAG_EMAIL": int(oui("email")),
        "OCCUPATION_TYPE": valeur_champ("metier"), "CNT_FAM_MEMBERS": float(valeur_champ("membres")),
        "AGE_ANNEES": float(valeur_champ("age")), "SANS_EMPLOI": int(sans_emploi),
        "ANCIENNETE_ANNEES": 0.0 if sans_emploi else float(valeur_champ("anciennete")),
    }


def action_evaluer():
    if champs_manquants():
        st.session_state.tentative = True
        st.session_state.defiler = True
        return
    x = construire_variables()
    resultat = evaluer_modele(x)
    resultat["variables"] = x
    st.session_state.resultat = resultat
    st.session_state.proposition_utilisee = False
    st.session_state.etape = "resultat"
    st.session_state.tentative = False
    st.session_state[f"decision_{st.session_state.get('version_form', 0)}"] = (
        "Refus" if resultat["recommandation"] == "refus" else "Accord")


def action_enregistrer(utilisateur):
    r = st.session_state.resultat
    v = st.session_state.get("version_form", 0)
    decision = "refus" if st.session_state.get(f"decision_{v}") == "Refus" else "accord"
    commentaire = (st.session_state.get(f"commentaire_{v}") or "").strip()
    date = maintenant()
    with bd() as con:
        # Une seule transaction : la demande, sa référence et la ligne de journal sont enregistrées ensemble, ou pas du tout
        did = con.execute(
            "INSERT INTO app.demandes (date, analyste, donnees, age, revenu, score, recommandation, decision, commentaire, facteurs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (date, utilisateur["identifiant"], json.dumps(r["variables"]), float(r["variables"]["AGE_ANNEES"]),
             float(r["variables"]["AMT_INCOME_TOTAL"]), float(r["score"]), r["recommandation"], decision, commentaire,
             json.dumps(r["facteurs"], ensure_ascii=False))).fetchone()["id"]
        reference = f"DEM-{did:06d}"
        con.execute("UPDATE app.demandes SET reference = %s WHERE id = %s", (reference, did))
        # Trace de l'aide à la rédaction : le commentaire reprend une proposition rédigée par l'IA
        assistant = r.get("assistant") or {}
        aide_ia = bool(commentaire) and st.session_state.get("proposition_utilisee", False) and assistant.get("source") == "ia"
        con.execute("INSERT INTO app.journal (demande_id, date, auteur, type, ancienne, nouvelle, commentaire, assistance_ia, modele_ia) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (did, date, utilisateur["identifiant"], "creation", None, decision, commentaire,
                     aide_ia, assistant.get("modele") if aide_ia else None))
    st.session_state.etape = "enregistree"
    st.session_state.ref_enregistree = reference
    st.session_state.decision_enregistree = decision


def page_nouvelle_demande(utilisateur):
    etat("version_form", 0)
    etape = etat("etape", "saisie")
    verrou = etape != "saisie"
    manquants = champs_manquants() if st.session_state.get("tentative") else []
    style_erreurs([cle_champ(n) for n in manquants])

    st.markdown("# Nouvelle demande")
    st.markdown('<div class="ns-sous-titre">Saisissez les informations du demandeur, puis cliquez sur « Évaluer la demande ». '
                'Tous les champs sont obligatoires.</div>', unsafe_allow_html=True)

    def champ(nom, libelle, type_, variable):
        cle = cle_champ(nom)
        if type_ == "liste":
            st.selectbox(libelle, options_liste(variable), index=None, placeholder="Sélectionner…",
                         format_func=lambda v, var=variable: traduire(var, v), key=cle, disabled=verrou)
        elif type_ == "ouinon":
            st.radio(libelle, ["Oui", "Non"], index=None, horizontal=True, key=cle, disabled=verrou)
        else:
            reglages = {"age": (18, 100, 1, "Ex. : 40"), "enfants": (0, 20, 1, "Ex. : 0"), "membres": (1, 20, 1, "Ex. : 2"),
                        "anciennete": (0, 60, 1, "Ex. : 5"), "revenu": (0, 100_000_000, 500, "Ex. : 150000")}[nom]
            inactif = verrou or (nom == "anciennete" and valeur_champ("sansemploi") == "Oui")
            if nom == "anciennete" and valeur_champ("sansemploi") == "Oui":
                st.session_state[cle] = None
            st.number_input(libelle, min_value=reglages[0], max_value=reglages[1], value=None, step=reglages[2],
                            placeholder="Sans objet" if nom == "anciennete" and valeur_champ("sansemploi") == "Oui" else reglages[3], key=cle, disabled=inactif)
        if nom in manquants:
            message_erreur("Choisissez Oui ou Non" if type_ == "ouinon" else "Champ obligatoire")

    with st.container(border=True):
        c1, c2, c3 = st.columns(3, gap="large")
        with c1:
            st.markdown("**PROFIL**")
            for nom in ("genre", "age", "etudes", "situation"):
                champ(*next(c for c in CHAMPS if c[0] == nom))
            sc1, sc2 = st.columns(2)
            with sc1:
                champ(*next(c for c in CHAMPS if c[0] == "enfants"))
            with sc2:
                champ(*next(c for c in CHAMPS if c[0] == "membres"))
        with c2:
            st.markdown("**EMPLOI ET REVENUS**")
            for nom in ("typerevenu", "metier", "sansemploi", "anciennete", "revenu"):
                champ(*next(c for c in CHAMPS if c[0] == nom))
        with c3:
            st.markdown("**PATRIMOINE ET CONTACT**")
            for nom in ("logement", "immo", "voiture", "telpro", "telfixe", "email"):
                champ(*next(c for c in CHAMPS if c[0] == nom))

        if manquants:
            n = len(manquants)
            titre = f"{n} champ est à compléter." if n == 1 else f"{n} champs sont à compléter."
            liste = " · ".join(LIBELLES_CHAMPS[m] for m in manquants)
            st.markdown(f'<div class="ns-recap" role="alert">{ICONE_ERREUR}<div><strong>{titre}</strong><br>'
                        f'<span>{liste}</span></div></div>', unsafe_allow_html=True)
            if st.session_state.pop("defiler", False):
                script = (f"<script>const c = window.parent.document.querySelector('.st-key-{cle_champ(manquants[0])}');"
                          "if (c) { c.scrollIntoView({behavior: 'smooth', block: 'center'}); "
                          "const e = c.querySelector('input'); if (e) e.focus({preventScroll: true}); }</script>")
                if hasattr(st, "iframe"):
                    st.iframe(script, height=1)
                else:
                    components.html(script, height=0)

        if etape == "saisie":
            _, b1, b2 = st.columns([4, 1, 2])
            b1.button("Effacer", key="btn_effacer", width="stretch", on_click=reinitialiser_demande)
            b2.button("Évaluer la demande", key="btn_evaluer", type="primary", width="stretch", on_click=action_evaluer)

    if etape == "saisie":
        return

    r = st.session_state.resultat
    refus = r["recommandation"] == "refus"
    couleur, fond = (ROUGE_FONCE, ROUGE_FOND) if refus else (VERT, VERT_FOND)
    titre = "Refus recommandé" if refus else "Accord recommandé"
    sous_titre = "Dossier à examiner par un analyste" if refus else "Le profil présente un risque modéré"
    icone = ('<path d="M12 3l9.5 17h-19z"></path><path d="M12 10v4.5M12 17.5v.01"></path>' if refus
             else '<circle cx="12" cy="12" r="9"></circle><path d="M8 12.5l2.8 2.8L16 9.5"></path>')
    score = r["score"]
    st.markdown(f"""
<div style="background:{fond};border:1px solid {BORDURE};border-radius:12px;padding:24px 28px;display:flex;flex-wrap:wrap;gap:24px;align-items:center;justify-content:space-between;margin-top:8px">
  <div style="display:flex;align-items:center;gap:16px">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="{couleur}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{icone}</svg>
    <div><div style="font-size:13px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;color:{TEXTE_2}">Recommandation</div>
    <div style="font-size:26px;font-weight:700;color:{couleur}">{titre}</div>
    <div style="font-size:15px">{sous_titre}</div></div>
  </div>
  <div style="flex:0 1 320px;min-width:240px">
    <div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:14px;font-weight:500;color:{TEXTE_2}">Score de risque</span>
    <span style="font-size:28px;font-weight:700">{score:.0f}<span style="font-size:15px;font-weight:500;color:{TEXTE_2}"> / 100</span></span></div>
    <div role="img" aria-label="Score de risque {score:.0f} sur 100" style="height:10px;border-radius:5px;background:#E5E9EF;overflow:hidden;margin:6px 0">
      <div style="width:{max(score, 1):.0f}%;height:100%;background:{ROUGE if refus else VERT};border-radius:5px"></div></div>
    <div style="display:flex;justify-content:space-between;font-size:12px;color:{TEXTE_2}"><span>Risque faible</span><span>Risque élevé</span></div>
  </div>
</div>""", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("### Pourquoi ce score ?")
        afficher_facteurs(r["facteurs"])

    with st.container(border=True):
        afficher_assistant(r)

    v = st.session_state.version_form
    with st.container(border=True):
        st.markdown("### Décision de l'analyste")
        if etape == "resultat":
            st.markdown('<div class="ns-sous-titre" style="margin-top:-6px">La décision est pré-remplie avec la recommandation. '
                        'Vous pouvez la modifier avant de l\'enregistrer.</div>', unsafe_allow_html=True)
            st.radio("Décision finale", ["Accord", "Refus"], horizontal=True, key=f"decision_{v}")
            st.text_area("Commentaire (facultatif)", placeholder="Ajoutez une précision pour l'historique.",
                         key=f"commentaire_{v}", height=90)
            _, b1, b2, b3 = st.columns([2, 1, 1.3, 1.6])
            b1.button("Annuler", key="btn_annuler", width="stretch", on_click=reinitialiser_demande)
            b2.button("Modifier la saisie", key="btn_modifier", width="stretch",
                      on_click=lambda: st.session_state.update(etape="saisie"))
            b3.button("Confirmer et enregistrer", key="btn_confirmer", type="primary", width="stretch",
                      on_click=action_enregistrer, args=(utilisateur,))
        else:
            decision = "Accord" if st.session_state.decision_enregistree == "accord" else "Refus"
            if st.session_state.decision_enregistree != r["recommandation"]:
                decision += " (différente de la recommandation)"
            message_succes(f"Demande enregistrée · Réf. {st.session_state.ref_enregistree} — Décision finale : {decision}.")
            _, b1, b2 = st.columns([3, 1.2, 1.2])
            b1.button("Voir l'historique", key="btn_voir_hist", width="stretch", on_click=aller, args=("historique",))
            b2.button("Nouvelle demande", key="btn_nouvelle", type="primary", width="stretch", on_click=reinitialiser_demande)


# ---------------------------------------------------------------- page : historique
LIBELLES_PERIODE = {"7": "7 derniers jours", "30": "30 derniers jours", "tout": "Depuis le début"}
LIBELLES_FILTRE = {"toutes": "Toutes", "accord": "Accord", "refus": "Refus",
                   "differentes": "Différentes de la recommandation", "corrigees": "Corrigées après enregistrement"}


def filtrer_historique(df, periode, decision, recherche):
    df = filtrer_periode(df, periode)
    if decision in ("accord", "refus"):
        df = df[df["decision"] == decision]
    elif decision == "differentes":
        df = df[df["decision"] != df["recommandation"]]
    elif decision == "corrigees":
        df = df[df["corrigee"] == 1]
    terme = (recherche or "").strip().lower()
    if terme:
        df = df[df["reference"].str.lower().str.contains(terme, regex=False) | df["analyste"].str.lower().str.contains(terme, regex=False)]
    return df


def tableau_affichage(df):
    return pd.DataFrame({
        "Date et heure": df["date_dt"].dt.strftime("%d/%m/%Y %H:%M"),
        "Référence": df["reference"],
        "Âge": df["age"].round().astype(int),
        "Revenu annuel": df["revenu"].map(nombre_fr),
        "Score": df["score"].round().astype(int),
        "Recommandation": df["recommandation"].map({"refus": "Refus recommandé", "accord": "Accord recommandé"}),
        "Décision finale": df["decision"].map({"refus": "Refus", "accord": "Accord"}),
        "Statut": [", ".join(s for s, ok in (("Modifiée", d != r), ("Corrigée", c == 1)) if ok)
                   for d, r, c in zip(df["decision"], df["recommandation"], df["corrigee"])],
        "Analyste": df["analyste"],
    })


def reinitialiser_filtres():
    st.session_state.f_periode = "tout"
    st.session_state.f_decision = "toutes"
    st.session_state.f_recherche = ""


def action_corriger(demande, utilisateur):
    nouvelle = "refus" if st.session_state.get("corr_decision") == "Refus" else "accord"
    motif = (st.session_state.get("corr_motif") or "").strip()
    if not motif:
        st.session_state.corr_erreur = True
        return
    with bd() as con:
        con.execute("UPDATE app.demandes SET decision = %s, corrigee = TRUE WHERE id = %s", (nouvelle, int(demande["id"])))
        con.execute("INSERT INTO app.journal (demande_id, date, auteur, type, ancienne, nouvelle, commentaire) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (int(demande["id"]), maintenant(), utilisateur["identifiant"], "correction", demande["decision"], nouvelle, motif))
    st.session_state.corr_ouvert = None
    st.session_state.corr_erreur = False
    st.session_state.corr_succes = demande["reference"]


def page_historique(utilisateur):
    st.markdown("# Historique des demandes")
    st.markdown('<div class="ns-sous-titre">Toutes les demandes évaluées, de la plus récente à la plus ancienne. '
                'Sélectionnez une demande pour voir son détail ou corriger sa décision.</div>', unsafe_allow_html=True)
    etat("f_periode", "30")
    etat("f_decision", "toutes")
    etat("f_recherche", "")

    toutes = charger_demandes()
    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns([1.6, 2, 2, 1.1, 1.4], vertical_alignment="bottom")
        c1.selectbox("Période", list(LIBELLES_PERIODE), format_func=LIBELLES_PERIODE.get, key="f_periode")
        c2.selectbox("Décision finale", list(LIBELLES_FILTRE), format_func=LIBELLES_FILTRE.get, key="f_decision")
        c3.text_input("Rechercher", placeholder="Référence ou analyste", key="f_recherche")
        c4.button("Réinitialiser", key="btn_reinit_filtres", width="stretch", on_click=reinitialiser_filtres)
        filtre = filtrer_historique(toutes, st.session_state.f_periode, st.session_state.f_decision, st.session_state.f_recherche)
        export = tableau_affichage(filtre) if not filtre.empty else pd.DataFrame()
        c5.download_button("Exporter en CSV", export.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           file_name=f"historique_novascore_{maintenant():%Y%m%d}.csv", mime="text/csv",
                           key="btn_export", icon=":material/download:", width="stretch", disabled=filtre.empty)

    if st.session_state.get("corr_succes"):
        message_succes(f"Modification enregistrée pour la demande {st.session_state.pop('corr_succes')}.")

    if toutes.empty:
        st.markdown('<div class="ns-info">Aucune demande n\'a encore été enregistrée. Les demandes évaluées depuis '
                    '« Nouvelle demande » apparaîtront ici.</div>', unsafe_allow_html=True)
        return
    if filtre.empty:
        st.markdown('<div class="ns-info"><strong style="color:#1B2430">Aucune demande ne correspond aux filtres.</strong><br>'
                    'Modifiez la période, la décision ou la recherche, ou réinitialisez les filtres.</div>', unsafe_allow_html=True)
        st.caption(f"Affichage de 0 demande sur {len(toutes)}")
        return

    affichage = tableau_affichage(filtre)
    selection = st.dataframe(
        affichage, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="table_historique",
        column_config={"Score": st.column_config.NumberColumn(format="%d"),
                       "Âge": st.column_config.NumberColumn(format="%d")})
    st.caption(f"Affichage de {len(filtre)} demande(s) sur {len(toutes)} · cliquez sur une ligne pour voir son détail.")

    lignes = selection.selection.rows if selection and selection.selection else []
    demande = filtre.iloc[lignes[0]] if lignes and lignes[0] < len(filtre) else filtre.iloc[0]
    if st.session_state.get("corr_ouvert") not in (None, demande["reference"]):
        st.session_state.corr_ouvert = None

    refus_reco = demande["recommandation"] == "refus"
    refus_dec = demande["decision"] == "refus"
    with st.container(border=True):
        t1, t2 = st.columns([3, 1.3], vertical_alignment="center")
        t1.markdown(f"### Détail de la demande {demande['reference']}")
        t1.caption(f"{demande['date_dt']:%d/%m/%Y %H:%M} · analyste : {demande['analyste']}")
        if st.session_state.get("corr_ouvert") is None:
            t2.button("Modifier la décision", key="btn_corr_ouvrir", icon=":material/edit:", width="stretch",
                      on_click=lambda ref=demande["reference"], dec=demande["decision"]: st.session_state.update(
                          corr_ouvert=ref, corr_erreur=False, corr_motif="",
                          corr_decision="Accord" if dec == "refus" else "Refus"))

        cartes = [("Score de risque", f"{demande['score']:.0f} / 100", TEXTE),
                  ("Recommandation", "Refus recommandé" if refus_reco else "Accord recommandé", ROUGE_FONCE if refus_reco else VERT),
                  ("Décision finale", "Refus" if refus_dec else "Accord", ROUGE_FONCE if refus_dec else VERT),
                  ("Profil", f"{demande['age']:.0f} ans · revenu {nombre_fr(demande['revenu'])}", TEXTE)]
        cols = st.columns(4)
        for col, (lib, val, coul) in zip(cols, cartes):
            col.markdown(f'<div style="background:#F7F9FB;border-radius:10px;padding:14px"><div style="font-size:13px;color:{TEXTE_2}">'
                         f'{lib}</div><div style="font-size:17px;font-weight:600;color:{coul}">{val}</div></div>', unsafe_allow_html=True)

        if st.session_state.get("corr_ouvert") == demande["reference"]:
            with st.container(border=True):
                st.markdown("#### Modifier la décision finale")
                st.radio("Nouvelle décision", ["Accord", "Refus"], horizontal=True, key="corr_decision")
                if st.session_state.get("corr_erreur"):
                    style_erreurs(["corr_motif"])
                st.text_area("Motif de la modification (obligatoire)", key="corr_motif", height=90,
                             placeholder="Exemple : erreur de saisie sur le revenu, dossier réexaminé avec les justificatifs.")
                if st.session_state.get("corr_erreur"):
                    message_erreur("Indiquez le motif de la modification avant d'enregistrer.")
                _, b1, b2 = st.columns([3, 1, 1.6])
                b1.button("Annuler", key="btn_corr_annuler", width="stretch",
                          on_click=lambda: st.session_state.update(corr_ouvert=None, corr_erreur=False))
                b2.button("Enregistrer la modification", key="btn_corr_valider", type="primary", width="stretch",
                          on_click=action_corriger, args=(demande, utilisateur))

        g, d = st.columns(2, gap="large")
        with g:
            st.markdown("#### Principaux facteurs du score")
            for f in json.loads(demande["facteurs"])[:5]:
                hausse = f["contribution"] > 0
                couleur_f = ROUGE_FONCE if hausse else BLEU
                st.markdown(f'<div style="display:flex;gap:10px;font-size:14px;margin-bottom:8px">'
                            f'<span style="min-width:48px;text-align:right;font-weight:600;color:{couleur_f};'
                            f'font-variant-numeric:tabular-nums">{valeur_shap_fr(f["contribution"])}</span>'
                            f'<span style="min-width:160px;font-weight:600;color:{couleur_f}">'
                            f'{"↑ Augmente le risque" if hausse else "↓ Diminue le risque"}</span><span>{f["libelle"]}</span></div>',
                            unsafe_allow_html=True)
        with d:
            st.markdown("#### Suivi des décisions")
            with bd() as con:
                journal = con.execute("SELECT * FROM app.journal WHERE demande_id = %s ORDER BY id", (int(demande["id"]),)).fetchall()
            for j in journal:
                nouvelle = "Refus" if j["nouvelle"] == "refus" else "Accord"
                if j["type"] == "creation":
                    titre = f"Décision enregistrée : {nouvelle}"
                    if j["nouvelle"] != demande["recommandation"]:
                        titre += " (différente de la recommandation)"
                    pastille = ROUGE if j["nouvelle"] == "refus" else VERT
                else:
                    ancienne = "Refus" if j["ancienne"] == "refus" else "Accord"
                    titre, pastille = f"Décision corrigée : {ancienne} → {nouvelle}", "#1F4E8C"
                commentaire = f'<div>« {html.escape(j["commentaire"])} »</div>' if j["commentaire"] else ""
                if j.get("assistance_ia"):
                    commentaire += (f'<div style="font-size:13px;color:{ORANGE_TEXTE}">Commentaire rédigé avec l\'aide de l\'IA'
                                    f' ({html.escape(j["modele_ia"] or "")}), validé par l\'analyste</div>')
                st.markdown(f'<div style="display:flex;gap:12px;font-size:14px;margin-bottom:10px">'
                            f'<span class="ns-pastille" style="background:{pastille};margin-top:5px;flex:0 0 10px"></span>'
                            f'<div><strong>{titre}</strong><div style="color:{TEXTE_2}">'
                            f'{j["date"].astimezone(FUSEAU):%d/%m/%Y %H:%M} · {j["auteur"]}</div>{commentaire}</div></div>',
                            unsafe_allow_html=True)


# ---------------------------------------------------------------- page : tableau de bord
def figure_base(fig, hauteur=300):
    fig.update_layout(height=hauteur, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                      font=dict(family="IBM Plex Sans, sans-serif", size=13, color=TEXTE), showlegend=False, separators=", ",
                      hoverlabel=dict(font_family="IBM Plex Sans, sans-serif"))
    fig.update_xaxes(showgrid=False, linecolor="#C5CED9")
    fig.update_yaxes(gridcolor="#EEF1F5", zeroline=False)
    return fig


def legende(elements):
    html = '<div class="ns-legende">' + "".join(
        f'<span><span class="ns-pastille" style="background:{c};border-radius:{"5px" if rond else "2px"}"></span>{t}</span>'
        for t, c, rond in elements) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def page_tableau_de_bord():
    t1, t2 = st.columns([3, 1], vertical_alignment="bottom")
    t1.markdown("# Tableau de bord")
    t1.markdown('<div class="ns-sous-titre">Indicateurs de pilotage des demandes de crédit</div>', unsafe_allow_html=True)
    etat("tdb_periode", "7")
    t2.selectbox("Période", list(LIBELLES_PERIODE), format_func=LIBELLES_PERIODE.get, key="tdb_periode")

    df = filtrer_periode(charger_demandes(), st.session_state.tdb_periode)
    if df.empty:
        st.markdown('<div class="ns-info">Aucune demande sur la période. Les indicateurs s\'afficheront dès que des demandes '
                    'auront été évaluées et enregistrées.</div>', unsafe_allow_html=True)
        return

    seuil = charger_modele()["seuil"] * 100
    n = len(df)
    nb_accord = int((df["decision"] == "accord").sum())
    nb_refus = n - nb_accord
    nb_modif = int((df["decision"] != df["recommandation"]).sum())
    libelle_periode = {"7": "sur les 7 derniers jours", "30": "sur les 30 derniers jours", "tout": "depuis le début"}[st.session_state.tdb_periode]

    kpis = [("Demandes traitées", str(n), libelle_periode, MARINE, None),
            ("Accords", pct_fr(nb_accord / n), f"{nb_accord} demande(s) accordée(s)", VERT, VERT),
            ("Refus", pct_fr(nb_refus / n), f"{nb_refus} demande(s) refusée(s)", ROUGE_FONCE, ROUGE),
            ("Décisions modifiées", pct_fr(nb_modif / n), f"{nb_modif} décision(s) différente(s) de la recommandation", ORANGE_TEXTE, "#B45309")]
    for col, (lib, val, detail, coul, pastille) in zip(st.columns(4), kpis):
        p = f'<span class="ns-pastille" style="background:{pastille}"></span>' if pastille else ""
        col.markdown(f'<div class="ns-carte"><div class="ns-kpi-libelle">{p}{lib}</div><div class="ns-kpi-valeur" style="color:{coul}">'
                     f'{val}</div><div class="ns-kpi-detail">{detail}</div></div>', unsafe_allow_html=True)
    st.write("")
    config = {"displayModeBar": False}

    g1, g2 = st.columns(2, gap="medium")
    with g1, st.container(border=True):
        st.markdown("##### Répartition des décisions finales")
        parts = [(l, v, c) for l, v, c in (("Accord", nb_accord, VERT), ("Refus", nb_refus, ROUGE)) if v > 0]
        fig = go.Figure(go.Pie(labels=[p[0] for p in parts], values=[p[1] for p in parts], hole=0.62, sort=False,
                               marker=dict(colors=[p[2] for p in parts], line=dict(color="#FFFFFF", width=2)),
                               textinfo="percent", texttemplate="%{percent:.1%}", hovertemplate="%{label} : %{value} demande(s)<extra></extra>"))
        fig.add_annotation(text=f"<b>{n}</b><br>demandes", showarrow=False, font=dict(size=16))
        st.plotly_chart(figure_base(fig), config=config, width="stretch", key="g_repartition")
        legende([("Accord", VERT, False), ("Refus", ROUGE, False)])
    with g2, st.container(border=True):
        st.markdown("##### Distribution des scores de risque")
        bacs = np.minimum((df["score"] // 10).astype(int), 9)
        x = [i * 10 + 5 for i in range(10)]
        acc = [int(((bacs == i) & (df["recommandation"] == "accord")).sum()) for i in range(10)]
        ref = [int(((bacs == i) & (df["recommandation"] == "refus")).sum()) for i in range(10)]
        fig = go.Figure([go.Bar(x=x, y=acc, marker_color=VERT, width=8.6, name="Accord recommandé",
                                customdata=[f"{i * 10} à {i * 10 + 10}" for i in range(10)],
                                hovertemplate="Scores %{customdata} : %{y} accord(s) recommandé(s)<extra></extra>"),
                         go.Bar(x=x, y=ref, marker_color=ROUGE, width=8.6, name="Refus recommandé",
                                customdata=[f"{i * 10} à {i * 10 + 10}" for i in range(10)],
                                hovertemplate="Scores %{customdata} : %{y} refus recommandé(s)<extra></extra>")])
        fig.update_layout(barmode="stack")
        fig.add_vline(x=seuil, line_dash="dash", line_color=TEXTE, line_width=2)
        fig.update_xaxes(range=[0, 100], tickvals=[0, 25, 50, 75, 100], title_text="Score de risque (sur 100)")
        fig.update_yaxes(title_text="Nombre de demandes", dtick=1 if max(np.add(acc, ref)) <= 10 else None)
        st.plotly_chart(figure_base(fig), config=config, width="stretch", key="g_distribution")
        legende([("Accord recommandé", VERT, False), ("Refus recommandé", ROUGE, False), ("Seuil de recommandation (pointillés)", TEXTE, False)])

    g3, g4 = st.columns(2, gap="medium")
    with g3, st.container(border=True):
        st.markdown("##### Demandes par jour")
        jours = df.assign(jour=df["date_dt"].dt.normalize()).groupby(["jour", "decision"]).size().unstack(fill_value=0)
        jours = jours.reindex(columns=["accord", "refus"], fill_value=0).sort_index()
        etiquettes = [j.strftime("%d/%m") for j in jours.index]
        fig = go.Figure([go.Bar(x=etiquettes, y=jours["accord"], marker_color=VERT, name="Accord",
                                hovertemplate="%{x} : %{y} accord(s)<extra></extra>"),
                         go.Bar(x=etiquettes, y=jours["refus"], marker_color=ROUGE, name="Refus",
                                hovertemplate="%{x} : %{y} refus<extra></extra>")])
        fig.update_layout(barmode="stack", bargap=0.35)
        fig.update_xaxes(type="category")
        fig.update_yaxes(title_text="Nombre de demandes", dtick=1 if jours.sum(axis=1).max() <= 10 else None)
        st.plotly_chart(figure_base(fig), config=config, width="stretch", key="g_jours")
        legende([("Accord", VERT, False), ("Refus", ROUGE, False)])
    with g4, st.container(border=True):
        st.markdown("##### Recommandation et décision finale")
        aa = int(((df["recommandation"] == "accord") & (df["decision"] == "accord")).sum())
        ar = int(((df["recommandation"] == "accord") & (df["decision"] == "refus")).sum())
        ra = int(((df["recommandation"] == "refus") & (df["decision"] == "accord")).sum())
        rr = int(((df["recommandation"] == "refus") & (df["decision"] == "refus")).sum())
        st.markdown(f'<p style="font-size:14px;color:{TEXTE_2}">Les analystes ont suivi la recommandation dans '
                    f'<strong style="color:{TEXTE}">{pct_fr((aa + rr) / n)}</strong> des cas ({aa + rr} sur {n}).</p>'
                    f'<table class="ns-tableau"><thead><tr><th style="text-align:left">Recommandation - Décision</th>'
                    f'<th style="color:{VERT}">Accord</th><th style="color:{ROUGE_FONCE}">Refus</th><th>Total</th></tr></thead><tbody>'
                    f'<tr><th style="text-align:left;font-weight:500">Accord recommandé</th><td style="text-align:center;background:{VERT_FOND};font-weight:600">{aa}</td>'
                    f'<td style="text-align:center;background:{ORANGE_FOND};color:{ORANGE_TEXTE};font-weight:600">{ar}</td><td style="text-align:center">{aa + ar}</td></tr>'
                    f'<tr><th style="text-align:left;font-weight:500">Refus recommandé</th><td style="text-align:center;background:{ORANGE_FOND};color:{ORANGE_TEXTE};font-weight:600">{ra}</td>'
                    f'<td style="text-align:center;background:{ROUGE_FOND};font-weight:600">{rr}</td><td style="text-align:center">{ra + rr}</td></tr>'
                    f'<tr><th style="text-align:left;font-weight:500;color:{TEXTE_2}">Total</th><td style="text-align:center">{nb_accord}</td>'
                    f'<td style="text-align:center">{nb_refus}</td><td style="text-align:center;font-weight:600">{n}</td></tr></tbody></table>',
                    unsafe_allow_html=True)
        legende([("Décisions modifiées par l'analyste", ORANGE_FOND, False)])

    g5, g6 = st.columns(2, gap="medium")
    with g5, st.container(border=True):
        st.markdown("##### Score de risque selon l'âge")
        fig = go.Figure()
        for reco, coul, nom in (("accord", VERT, "Accord recommandé"), ("refus", ROUGE, "Refus recommandé")):
            sous = df[df["recommandation"] == reco]
            fig.add_trace(go.Scatter(x=sous["age"], y=sous["score"], mode="markers", name=nom, customdata=sous["reference"],
                                     marker=dict(color=coul, size=11, line=dict(color="#FFFFFF", width=2)),
                                     hovertemplate="%{customdata} · %{x:.0f} ans · score %{y:.0f}<extra></extra>"))
        fig.add_hline(y=seuil, line_dash="dash", line_color=TEXTE, line_width=2)
        fig.update_xaxes(title_text="Âge (années)", range=[18, 75])
        fig.update_yaxes(title_text="Score de risque", range=[0, 100])
        st.plotly_chart(figure_base(fig), config=config, width="stretch", key="g_age")
        legende([("Accord recommandé", VERT, True), ("Refus recommandé", ROUGE, True)])
    with g6, st.container(border=True):
        st.markdown("##### Principaux facteurs de risque dans les refus")
        st.markdown(f'<p style="font-size:14px;color:{TEXTE_2}">Nombre de refus recommandés où le facteur est le premier motif</p>',
                    unsafe_allow_html=True)
        compte = {}
        for facteurs in df.loc[df["recommandation"] == "refus", "facteurs"]:
            positifs = [f for f in json.loads(facteurs) if f["contribution"] > 0]
            if positifs:
                lib = VARIABLES[positifs[0]["variable"]]
                compte[lib] = compte.get(lib, 0) + 1
        if not compte:
            st.markdown(f'<p style="font-size:14px;color:{TEXTE_2}">Aucun refus recommandé sur la période.</p>', unsafe_allow_html=True)
        else:
            serie = pd.Series(compte).sort_values(ascending=True)
            fig = go.Figure(go.Bar(x=serie.values, y=serie.index, orientation="h", marker_color=BLEU, text=serie.values,
                                   textposition="outside", hovertemplate="%{y} : %{x}<extra></extra>"))
            fig.update_xaxes(dtick=1, title_text="Nombre de refus recommandés", range=[0, serie.max() * 1.2])
            st.plotly_chart(figure_base(fig, max(200, 60 + 44 * len(serie))), config=config, width="stretch", key="g_facteurs")


# ---------------------------------------------------------------- page : utilisateurs
RE_IDENTIFIANT = r"^[a-z0-9.-]{3,30}$"


def erreurs_utilisateur(mode, cible, utilisateurs):
    import re
    e = {}
    ident = (st.session_state.get("u_identifiant") or "").strip().lower()
    mdp = st.session_state.get("u_mdp") or ""
    conf = st.session_state.get("u_conf") or ""
    role = st.session_state.get("u_role")
    if not ident:
        e["u_identifiant"] = "Champ obligatoire"
    elif not re.match(RE_IDENTIFIANT, ident):
        e["u_identifiant"] = "3 à 30 caractères : lettres minuscules, chiffres, point ou tiret"
    elif any(u["identifiant"] == ident and u["id"] != (cible["id"] if cible else None) for u in utilisateurs):
        e["u_identifiant"] = "Cet identifiant existe déjà"
    if mode == "creation" or mdp or conf:
        if not mdp:
            e["u_mdp"] = "Champ obligatoire"
        elif len(mdp) < 8:
            e["u_mdp"] = "8 caractères minimum"
        if not conf:
            e["u_conf"] = "Champ obligatoire"
        elif conf != mdp:
            e["u_conf"] = "Les mots de passe ne correspondent pas"
    if not role:
        e["u_role"] = "Champ obligatoire"
    elif cible and cible["role"] == "admin" and role != "admin" and sum(u["role"] == "admin" for u in utilisateurs) <= 1:
        e["u_role"] = "Il doit rester au moins un administrateur"
    return e


def vider_formulaire_utilisateur():
    st.session_state.update(u_mode="creation", u_cible=None, u_identifiant="", u_mdp="", u_conf="", u_role=None, u_tentative=False)


def action_utilisateur(moi):
    utilisateurs = lister_utilisateurs()
    mode = st.session_state.u_mode
    cible = next((u for u in utilisateurs if u["id"] == st.session_state.u_cible), None) if mode == "edition" else None
    if erreurs_utilisateur(mode, cible, utilisateurs):
        st.session_state.u_tentative = True
        st.session_state.u_succes = ""
        return
    ident = st.session_state.u_identifiant.strip().lower()
    role, mdp = st.session_state.u_role, st.session_state.u_mdp
    with bd() as con:
        if mode == "creation":
            sel, emp = hacher(mdp)
            con.execute("INSERT INTO app.utilisateurs (identifiant, role, sel, empreinte, cree_le, cree_par) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (ident, role, sel, emp, maintenant(), moi["identifiant"]))
            message = f"Utilisateur « {ident} » créé. Il peut se connecter dès maintenant."
        else:
            changements = []
            if ident != cible["identifiant"]:
                changements.append("identifiant")
            if role != cible["role"]:
                changements.append("rôle")
            sel, emp = (hacher(mdp) if mdp else (cible["sel"], cible["empreinte"]))
            if mdp:
                changements.append("mot de passe")
            if changements:
                con.execute("UPDATE app.utilisateurs SET identifiant = %s, role = %s, sel = %s, empreinte = %s, "
                            "modifie_le = %s WHERE id = %s",
                            (ident, role, sel, emp, maintenant(), cible["id"]))
                message = f"Utilisateur « {ident} » modifié ({', '.join(changements)})."
            else:
                message = "Aucune modification à enregistrer."
    vider_formulaire_utilisateur()
    st.session_state.u_succes = message
    st.session_state.u_surbrillance = ident


def ouvrir_edition(u):
    st.session_state.update(u_mode="edition", u_cible=u["id"], u_identifiant=u["identifiant"], u_mdp="", u_conf="",
                            u_role=u["role"], u_tentative=False, u_succes="", u_supprimer=None)


def supprimer_utilisateur(uid):
    with bd() as con:
        u = con.execute("SELECT identifiant FROM app.utilisateurs WHERE id = %s", (uid,)).fetchone()
        con.execute("DELETE FROM app.utilisateurs WHERE id = %s", (uid,))
    if st.session_state.get("u_cible") == uid:
        vider_formulaire_utilisateur()
    st.session_state.u_supprimer = None
    st.session_state.u_succes = f"Utilisateur « {u['identifiant']} » supprimé."


def page_utilisateurs(moi):
    if moi["role"] != "admin":  # contrôle côté serveur, indépendant du menu
        st.markdown('<div class="ns-info"><strong style="color:#1B2430;font-size:20px">Accès réservé aux administrateurs</strong>'
                    '<br>Votre compte ne permet pas de gérer les utilisateurs.</div>', unsafe_allow_html=True)
        return
    for cle, defaut in (("u_mode", "creation"), ("u_cible", None), ("u_identifiant", ""), ("u_mdp", ""), ("u_conf", ""),
                        ("u_role", None), ("u_tentative", False), ("u_succes", ""), ("u_supprimer", None), ("u_surbrillance", "")):
        etat(cle, defaut)
    utilisateurs = lister_utilisateurs()
    edition = st.session_state.u_mode == "edition"
    cible = next((u for u in utilisateurs if u["id"] == st.session_state.u_cible), None) if edition else None
    if edition and cible is None:
        vider_formulaire_utilisateur()
        edition = False
    soi = edition and cible["id"] == moi["id"]
    erreurs = erreurs_utilisateur(st.session_state.u_mode, cible, utilisateurs) if st.session_state.u_tentative else {}
    style_erreurs(list(erreurs))

    st.markdown("# Gestion des utilisateurs")
    st.markdown('<div class="ns-sous-titre">Créez, modifiez ou supprimez les comptes qui peuvent se connecter à NovaScore.</div>',
                unsafe_allow_html=True)
    if st.session_state.u_succes:
        message_succes(st.session_state.u_succes)

    gauche, droite = st.columns([1, 2], gap="large")
    with gauche, st.container(border=True):
        st.markdown(f"### {'Modifier l’utilisateur « ' + cible['identifiant'] + ' »' if edition else 'Créer un utilisateur'}")
        if edition:
            st.caption("Laissez le mot de passe vide pour le conserver.")
        st.text_input("Identifiant", key="u_identifiant", placeholder="Ex. : m.durand", disabled=soi)
        if "u_identifiant" in erreurs:
            message_erreur(erreurs["u_identifiant"])
        else:
            st.caption("Votre propre identifiant ne peut pas être modifié." if soi
                       else "3 à 30 caractères : lettres minuscules, chiffres, point ou tiret.")
        st.text_input("Nouveau mot de passe" if edition else "Mot de passe", type="password", key="u_mdp",
                      placeholder="8 caractères minimum")
        if "u_mdp" in erreurs:
            message_erreur(erreurs["u_mdp"])
        st.text_input("Confirmer le mot de passe", type="password", key="u_conf", placeholder="Saisissez-le à nouveau")
        if "u_conf" in erreurs:
            message_erreur(erreurs["u_conf"])
        st.selectbox("Rôle", ["analyste", "admin"], index=None, placeholder="Sélectionner…", key="u_role", disabled=soi,
                     format_func=lambda r: "Administrateur" if r == "admin" else "Analyste")
        if "u_role" in erreurs:
            message_erreur(erreurs["u_role"])
        else:
            st.caption("Votre propre rôle ne peut pas être modifié." if soi
                       else "Un administrateur a aussi accès à la gestion des utilisateurs.")
        b1, b2 = st.columns([1, 1.6])
        b1.button("Annuler" if edition else "Effacer", key="btn_u_annuler", width="stretch",
                  on_click=lambda: (vider_formulaire_utilisateur(), st.session_state.update(u_succes="")))
        b2.button("Enregistrer les modifications" if edition else "Créer l'utilisateur", key="btn_u_valider",
                  type="primary", width="stretch", on_click=action_utilisateur, args=(moi,))

    with droite, st.container(border=True):
        st.markdown(f"### Utilisateurs <span style='font-size:14px;font-weight:400;color:{TEXTE_2}'>· {len(utilisateurs)} compte(s)</span>",
                    unsafe_allow_html=True)
        a_supprimer = next((u for u in utilisateurs if u["id"] == st.session_state.u_supprimer), None)
        if a_supprimer:
            with st.container(border=True):
                st.markdown(f'<strong style="color:{ROUGE_FONCE}">Supprimer le compte « {a_supprimer["identifiant"]} » ?</strong><br>'
                            "<span style='font-size:14px'>Cette personne ne pourra plus se connecter. "
                            "Les demandes qu'elle a traitées restent dans l'historique.</span>", unsafe_allow_html=True)
                _, c1, c2 = st.columns([1.5, 1, 1.6])
                c1.button("Annuler", key="btn_sup_annuler", width="stretch",
                          on_click=lambda: st.session_state.update(u_supprimer=None))
                c2.button("Supprimer définitivement", key="btn_sup_confirmer", type="primary", width="stretch",
                          on_click=supprimer_utilisateur, args=(a_supprimer["id"],))
        largeurs = [1.5, 1.5, 1.05, 1.05, 3.1]
        entete = st.columns(largeurs)
        for col, titre in zip(entete, ["Identifiant", "Rôle", "Créé le", "Modifié le", ""]):
            col.markdown(f"<span style='font-size:13px;font-weight:600;color:{TEXTE_2}'>{titre}</span>", unsafe_allow_html=True)
        nb_admins = sum(u["role"] == "admin" for u in utilisateurs)
        for u in utilisateurs:
            c = st.columns(largeurs, vertical_alignment="center")
            nom = u["identifiant"] + (" <span style='font-weight:400;color:#4A5565'>(vous)</span>" if u["id"] == moi["id"] else "")
            fond = "#EEF7F1" if u["identifiant"] == st.session_state.u_surbrillance else "transparent"
            c[0].markdown(f"<div style='font-weight:600;background:{fond};border-radius:6px;padding:2px 6px'>{nom}</div>",
                          unsafe_allow_html=True)
            admin = u["role"] == "admin"
            c[1].markdown(f"<span style='display:inline-block;white-space:nowrap;padding:3px 10px;border-radius:12px;font-size:13px;font-weight:600;"
                          f"background:{'#E8EEF7' if admin else '#F1F3F6'};color:{'#1F4E8C' if admin else TEXTE}'>"
                          f"{'Administrateur' if admin else 'Analyste'}</span>", unsafe_allow_html=True)
            c[2].markdown(f"<span style='font-size:14px;white-space:nowrap'>{u['cree_le'].astimezone(FUSEAU):%d/%m/%Y}</span>",
                          unsafe_allow_html=True)
            modif = u["modifie_le"].astimezone(FUSEAU).strftime("%d/%m/%Y") if u["modifie_le"] else "—"
            c[3].markdown(f"<span style='font-size:14px;color:{TEXTE_2}'>{modif}</span>", unsafe_allow_html=True)
            a1, a2 = c[4].columns(2)
            a1.button("Modifier", key=f"btn_mod_{u['id']}", width="stretch", on_click=ouvrir_edition, args=(u,))
            interdit = u["id"] == moi["id"] or (admin and nb_admins <= 1)
            a2.button("Supprimer", key=f"btn_sup_{u['id']}", width="stretch", disabled=interdit,
                      help="Vous ne pouvez pas supprimer votre propre compte" if u["id"] == moi["id"] else None,
                      on_click=lambda uid=u["id"]: st.session_state.update(u_supprimer=uid, u_succes=""))
        st.caption("Les mots de passe sont enregistrés de façon chiffrée : ils ne peuvent pas être affichés, seulement remplacés.")


# ---------------------------------------------------------------- programme principal
def main():
    appliquer_style()
    try:
        initialiser_base()
    except (psycopg.Error, PoolTimeout, OSError) as erreur:  # base injoignable, mot de passe refusé, réserve saturée
        page_base_indisponible(erreur)
        return
    utilisateur = utilisateur_courant()
    if utilisateur is None:
        page_connexion()
        return
    menu(utilisateur)
    page = st.session_state.get("page", "tableau")
    if page == "utilisateurs" and utilisateur["role"] != "admin":
        page = st.session_state.page = "tableau"
    if page == "tableau":
        page_tableau_de_bord()
    elif page == "nouvelle":
        page_nouvelle_demande(utilisateur)
    elif page == "historique":
        page_historique(utilisateur)
    elif page == "utilisateurs":
        page_utilisateurs(utilisateur)


main()
