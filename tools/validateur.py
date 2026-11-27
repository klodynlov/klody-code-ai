"""Validateur de schéma : un livrable JSON, JSONL ou CSV est-il conforme ?

Le contrôle qu'un agent fait AVANT de dire « c'est fait » : syntaxe d'abord, puis,
si un schéma est donné, conformité à un JSON Schema. Un seul langage de schéma
pour les trois formats : en CSV, chaque ligne est un objet {colonne: valeur}
validé contre le schéma, après conversion des cellules vers le type que le schéma
attend (une cellule CSV est toujours du texte).

Fonctions PURES sur du texte : la lecture des fichiers passe par le
``FileManager`` de l'orchestrateur (racines autorisées, extensions bloquées,
taille maximale) — ce module ne touche jamais au disque, ni au réseau : un
``$ref`` distant reste non résolu (jsonschema ≥ 4.18 ne télécharge rien).
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError, ValidationError

ERREURS_AFFICHEES = 20
# Au-delà, on arrête de compter : un fichier entièrement faux n'a pas besoin
# d'un inventaire exhaustif, et une validation doit rester bornée en temps.
ERREURS_MAX = 500
SCHEMA_MAX = 50_000
FORMATS = ("json", "jsonl", "csv", "tsv")
_EXTENSIONS = {".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
               ".csv": "csv", ".tsv": "tsv"}
_ENTIER = re.compile(r"^[+-]?\d+$")
_NOMBRE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")
_VRAI = {"true", "vrai", "oui", "yes", "1"}
_FAUX = {"false", "faux", "non", "no", "0"}


def format_de(chemin: str, impose: str = "") -> str | None:
    """Format demandé, sinon déduit de l'extension ; None si inconnu."""
    impose = (impose or "").strip().lower()
    if impose:
        return impose if impose in FORMATS else None
    suffixe = chemin.lower().rsplit(".", 1)
    return _EXTENSIONS.get("." + suffixe[-1]) if len(suffixe) == 2 else None


def charger_schema(texte: str) -> dict[str, Any] | str:
    """Schéma JSON depuis du texte ; une chaîne d'erreur nommée sinon."""
    if len(texte) > SCHEMA_MAX:
        return f"schéma trop long ({len(texte)} car., max {SCHEMA_MAX})."
    try:
        schema = json.loads(texte)
    except json.JSONDecodeError as e:
        return f"le schéma n'est pas du JSON valide (ligne {e.lineno}, colonne {e.colno} : {e.msg})."
    if not isinstance(schema, dict):
        return "le schéma doit être un objet JSON ({…})."
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except SchemaError as e:
        return f"le schéma lui-même est invalide : {e.message}"
    return schema


def _valideur(schema: dict[str, Any]) -> Any:
    classe = jsonschema.validators.validator_for(schema)
    return classe(schema, format_checker=classe.FORMAT_CHECKER)


def _chemin(err: ValidationError) -> str:
    parties = list(err.absolute_path)
    if not parties:
        return "racine"
    return "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parties).lstrip(".")


def _erreurs_schema(valideur: Any, donnee: Any) -> Iterator[ValidationError]:
    try:
        erreurs = list(valideur.iter_errors(donnee))
    except Exception as e:  # $ref non résoluble, motif regex invalide à l'exécution…
        raise ValueError(f"le schéma ne peut pas être appliqué ({e})") from e
    yield from sorted(erreurs, key=lambda e: [str(p) for p in e.absolute_path])


# ── JSON / JSONL ────────────────────────────────────────────────────────────

def _valider_json(texte: str, valideur: Any | None) -> tuple[list[str], str]:
    try:
        donnee = json.loads(texte)
    except json.JSONDecodeError as e:
        return [f"JSON invalide ligne {e.lineno}, colonne {e.colno} : {e.msg}"], ""
    taille = (f"{len(donnee)} élément(s)" if isinstance(donnee, list | dict)
              else type(donnee).__name__)
    if valideur is None:
        return [], taille
    erreurs = []
    for err in _erreurs_schema(valideur, donnee):
        erreurs.append(f"{_chemin(err)} : {err.message}")
        if len(erreurs) >= ERREURS_MAX:
            break
    return erreurs, taille


def _valider_jsonl(texte: str, valideur: Any | None) -> tuple[list[str], str]:
    erreurs: list[str] = []
    n = 0
    for numero, ligne in enumerate(texte.splitlines(), 1):
        if not ligne.strip():
            continue
        n += 1
        try:
            donnee = json.loads(ligne)
        except json.JSONDecodeError as e:
            erreurs.append(f"ligne {numero} : JSON invalide (colonne {e.colno} : {e.msg})")
        else:
            if valideur is not None:
                for err in _erreurs_schema(valideur, donnee):
                    erreurs.append(f"ligne {numero}, {_chemin(err)} : {err.message}")
        if len(erreurs) >= ERREURS_MAX:
            break
    return erreurs, f"{n} enregistrement(s)"


# ── CSV / TSV ───────────────────────────────────────────────────────────────

def _types(prop: Any) -> set[str]:
    t = prop.get("type") if isinstance(prop, dict) else None
    if isinstance(t, str):
        return {t}
    return {x for x in t if isinstance(x, str)} if isinstance(t, list) else set()


def _convertir(cellule: str, types: set[str]) -> Any:
    """Texte de cellule → valeur du type attendu, si elle s'y prête.

    Une cellule non convertible reste du TEXTE : c'est le schéma qui dira
    « 'abc' is not of type 'integer' », avec la valeur sous les yeux.
    """
    brut = cellule.strip()
    if brut == "" and "null" in types:
        return None
    if "integer" in types and _ENTIER.match(brut):
        return int(brut)
    if "number" in types and _NOMBRE.match(brut):
        return float(brut.replace(",", "."))
    if "boolean" in types and brut.lower() in _VRAI | _FAUX:
        return brut.lower() in _VRAI
    return cellule


def _delimiteur(texte: str, impose: str) -> str:
    if impose:
        return impose
    try:
        return csv.Sniffer().sniff(texte[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _valider_csv(texte: str, schema: dict[str, Any] | None,
                 delimiteur: str) -> tuple[list[str], str]:
    lecteur = csv.reader(io.StringIO(texte, newline=""), delimiter=delimiteur)
    try:
        entete = next(lecteur)
    except StopIteration:
        return ["fichier vide : pas même une ligne d'en-tête."], ""
    except csv.Error as e:
        return [f"ligne 1 : CSV illisible ({e})."], ""
    entete = [c.strip().lstrip("﻿") for c in entete]
    erreurs: list[str] = []
    vus: set[str] = set()
    for c in entete:
        if not c:
            erreurs.append("en-tête : une colonne n'a pas de nom.")
        elif c in vus:
            erreurs.append(f"en-tête : colonne « {c} » en double.")
        vus.add(c)

    proprietes: dict[str, Any] = {}
    if schema is not None:
        proprietes = schema.get("properties") or {}
        manquantes = [c for c in schema.get("required") or [] if c not in vus]
        if manquantes:
            erreurs.append("en-tête : colonne(s) obligatoire(s) absente(s) : "
                           + ", ".join(manquantes) + ".")
        if schema.get("additionalProperties") is False:
            en_trop = [c for c in entete if c and c not in proprietes]
            if en_trop:
                erreurs.append("en-tête : colonne(s) non prévue(s) par le schéma : "
                               + ", ".join(en_trop) + ".")
    # Les absences de colonnes sont déjà dites UNE fois sur l'en-tête : ne pas
    # les répéter à chaque ligne.
    schema_ligne = None
    if schema is not None:
        schema_ligne = dict(schema)
        schema_ligne["required"] = [c for c in schema.get("required") or [] if c in vus]
        if schema.get("additionalProperties") is False:
            schema_ligne.pop("additionalProperties")
    valideur = _valideur(schema_ligne) if schema_ligne is not None else None

    lignes = 0
    try:
        for ligne in lecteur:
            numero = lecteur.line_num
            if not any(c.strip() for c in ligne):
                continue
            lignes += 1
            if len(ligne) != len(entete):
                erreurs.append(f"ligne {numero} : {len(ligne)} cellule(s) pour "
                               f"{len(entete)} colonne(s).")
            elif valideur is not None:
                objet = {c: _convertir(v, _types(proprietes.get(c)))
                         for c, v in zip(entete, ligne, strict=True) if c}
                for err in _erreurs_schema(valideur, objet):
                    col = _chemin(err)
                    erreurs.append(f"ligne {numero}" + ("" if col == "racine" else f", colonne {col}")
                                   + f" : {err.message}")
            if len(erreurs) >= ERREURS_MAX:
                break
    except csv.Error as e:
        erreurs.append(f"ligne {lecteur.line_num} : CSV illisible ({e}).")
    nom_delim = {",": "virgule", ";": "point-virgule", "\t": "tabulation", "|": "barre"}
    return erreurs, (f"{lignes} ligne(s), {len(entete)} colonne(s), séparateur "
                     f"{nom_delim.get(delimiteur, repr(delimiteur))}")


# ── Entrée unique ───────────────────────────────────────────────────────────

def valider(texte: str, fmt: str, schema: dict[str, Any] | None = None,
            delimiteur: str = "") -> tuple[bool, list[str], str]:
    """(conforme, erreurs, résumé). Lève ValueError si la validation est impossible."""
    if fmt == "json":
        erreurs, resume = _valider_json(texte, _valideur(schema) if schema else None)
    elif fmt == "jsonl":
        erreurs, resume = _valider_jsonl(texte, _valideur(schema) if schema else None)
    elif fmt in ("csv", "tsv"):
        delim = "\t" if fmt == "tsv" and not delimiteur else _delimiteur(texte, delimiteur)
        erreurs, resume = _valider_csv(texte, schema, delim)
    else:
        raise ValueError(f"format « {fmt} » non pris en charge ({', '.join(FORMATS)}).")
    return not erreurs, erreurs, resume


def rapport(chemin: str, conforme: bool, erreurs: Iterable[str], resume: str,
            avec_schema: bool) -> str:
    """Texte rendu au modèle : verdict d'abord, puis les premières erreurs localisées."""
    erreurs = list(erreurs)
    portee = "conforme au schéma" if avec_schema else "syntaxe valide (aucun schéma fourni)"
    if conforme:
        return f"✅ {chemin} : {portee} — {resume}."
    plus = "+" if len(erreurs) >= ERREURS_MAX else ""
    lignes = [f"❌ {chemin} : {len(erreurs)}{plus} erreur(s)"
              + (f" — {resume}" if resume else "") + "."]
    lignes += [f"- {e}" for e in erreurs[:ERREURS_AFFICHEES]]
    if len(erreurs) > ERREURS_AFFICHEES:
        lignes.append(f"… et {len(erreurs) - ERREURS_AFFICHEES}{plus} autre(s).")
    lignes.append("Corrige le fichier, puis revalide-le avant de conclure.")
    return "\n".join(lignes)


def valider_fichier(lire: Any, chemin: str, schema: Any = "", schema_chemin: str = "",
                    format: str = "", delimiteur: str = "") -> str:
    """Outil ``valider_fichier`` : lit par ``lire`` (le ``read_file`` gardé de
    l'orchestrateur), valide, rend un rapport. Ne lève jamais : toute impossibilité
    est NOMMÉE — un « je n'ai pas pu juger » ne doit jamais se lire « conforme ».
    """
    chemin = str(chemin or "").strip()
    if not chemin:
        return "❌ Validation impossible : `chemin` (le fichier à valider) est vide."
    fmt = format_de(chemin, str(format or ""))
    if fmt is None:
        return (f"❌ Validation impossible : format de « {chemin} » inconnu. "
                f"Précise `format` ({', '.join(FORMATS)}).")
    delim = str(delimiteur or "")
    if len(delim) > 1:
        return "❌ Validation impossible : `delimiteur` doit être un seul caractère."
    try:
        texte = lire(chemin)
    except Exception as e:  # racine interdite, introuvable, trop gros…
        return f"❌ Validation impossible : lecture de « {chemin} » refusée ou échouée ({e})."

    schema_obj: dict[str, Any] | None = None
    brut: Any = schema
    if str(schema_chemin or "").strip():
        try:
            brut = lire(str(schema_chemin).strip())
        except Exception as e:
            return f"❌ Validation impossible : lecture du schéma « {schema_chemin} » échouée ({e})."
    if isinstance(brut, dict):
        brut = json.dumps(brut, ensure_ascii=False)
    if str(brut or "").strip():
        charge = charger_schema(str(brut))
        if isinstance(charge, str):
            return f"❌ Validation impossible : {charge}"
        schema_obj = charge
    try:
        conforme, erreurs, resume = valider(texte, fmt, schema_obj, delim)
    except ValueError as e:
        return f"❌ Validation impossible pour « {chemin} » : {e}"
    return rapport(chemin, conforme, erreurs, resume, schema_obj is not None)
