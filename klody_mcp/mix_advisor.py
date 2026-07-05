"""Conseil de mixage — cœurs PURS dérivés des mesures objectives (P-musique).

Prend en entrée les MÉTRIQUES produites par klody_mcp.audio_analysis.analyze_wav
(énergie par bandes, LUFS, largeur stéréo, crest…) et en tire des RECOMMANDATIONS
actionnables — jamais de nouveaux nombres inventés, juste une lecture experte des
chiffres mesurés. Trois briques, complémentaires des analyses déjà existantes :

  - recommander_eq        : compare le spectre à une courbe de RÉFÉRENCE par style et
    propose des mouvements EQ (creuser / renforcer, bande + plage Hz + ~dB indicatif).
  - detecter_masquage     : croise l'énergie par bandes de DEUX éléments (ex. voix vs
    instru) et repère les bandes où ils se marchent dessus (masquage fréquentiel).
  - analyser_balance_tonale : score global de proximité à la référence + verdict/bande.

Aucune dépendance lourde : ces fonctions travaillent sur des dicts Python (pas de
numpy). Toute courbe de référence est une HEURISTIQUE de point de départ (spec « ne
jamais prétendre ») : c'est une cible plausible pour dégrossir, pas un juge absolu.
Le contexte (voix, arrangement, intention) reste à l'oreille de l'appelant.
"""
from __future__ import annotations

# Bandes de audio_analysis._spectral (Hz) — mêmes bornes, pour que les conseils
# collent EXACTEMENT au champ band_energy mesuré.
_BANDES_HZ = {
    "sub": "0–120 Hz", "low": "120–500 Hz", "mid": "500–2000 Hz",
    "presence": "2–6 kHz", "air": "6 kHz+",
}
_ORDRE = ["sub", "low", "mid", "presence", "air"]

# Courbes de RÉFÉRENCE (fractions d'énergie par bande, normalisées à 1). Points de
# départ heuristiques — orientés vers les styles du studio (zouk / RnB / afro / trap
# soul / reggae) + neutre/pop génériques. À AFFINER à l'oreille, jamais un verdict.
_REFERENCES: dict[str, dict[str, float]] = {
    "neutre": {"sub": 0.10, "low": 0.30, "mid": 0.33, "presence": 0.17, "air": 0.10},
    "pop":    {"sub": 0.10, "low": 0.28, "mid": 0.30, "presence": 0.20, "air": 0.12},
    "rnb":    {"sub": 0.14, "low": 0.32, "mid": 0.28, "presence": 0.16, "air": 0.10},
    "zouk":   {"sub": 0.12, "low": 0.30, "mid": 0.30, "presence": 0.18, "air": 0.10},
    "afro":   {"sub": 0.13, "low": 0.30, "mid": 0.29, "presence": 0.18, "air": 0.10},
    "trap":   {"sub": 0.18, "low": 0.30, "mid": 0.26, "presence": 0.16, "air": 0.10},
    "reggae": {"sub": 0.16, "low": 0.34, "mid": 0.28, "presence": 0.14, "air": 0.08},
}
# Alias de style tolérés (entrée utilisateur libre).
_ALIAS = {
    "trap soul": "trap", "trapsoul": "trap", "r&b": "rnb", "r'n'b": "rnb",
    "dancehall": "reggae", "afrobeat": "afro", "afrobeats": "afro", "": "neutre",
    "tous": "neutre", "default": "neutre",
}

_TOL = 0.03  # tolérance (points de fraction) sous laquelle une bande est « OK »


def _normaliser(courbe: dict[str, float]) -> dict[str, float]:
    total = sum(courbe.get(b, 0.0) for b in _ORDRE) or 1.0
    return {b: courbe.get(b, 0.0) / total for b in _ORDRE}


def _resoudre_style(style: str) -> tuple[str, dict[str, float]]:
    """Nom de style libre -> (nom canonique, courbe de référence normalisée)."""
    s = (style or "").strip().lower()
    s = _ALIAS.get(s, s)
    if s not in _REFERENCES:
        s = "neutre"
    return s, _normaliser(_REFERENCES[s])


def _band_energy(analyse: dict) -> dict[str, float] | None:
    """Extrait et normalise band_energy d'une analyse. None si absent/vide."""
    if not isinstance(analyse, dict):
        return None
    be = analyse.get("band_energy")
    if not isinstance(be, dict) or not be:
        return None
    if sum(float(be.get(b, 0.0) or 0.0) for b in _ORDRE) <= 0:
        return None
    return _normaliser({b: float(be.get(b, 0.0) or 0.0) for b in _ORDRE})


def _db_indicatif(courant: float, cible: float) -> float:
    """Écart ~dB entre l'énergie mesurée et la cible, borné à ±4 dB (geste doux).

    10·log10(rapport de PUISSANCE). Indicatif : un point de départ de geste EQ, pas
    une valeur à recopier aveuglément (dépend de la largeur Q et du matériau)."""
    import math
    if courant <= 0 or cible <= 0:
        return 0.0
    return round(max(-4.0, min(4.0, 10.0 * math.log10(courant / cible))), 1)


def recommander_eq(analyse: dict, style: str = "neutre") -> dict:
    """Propose des mouvements EQ en comparant le spectre mesuré à une référence de style.

    `analyse` = sortie de audio_analysis.analyze_wav (doit contenir band_energy).
    Pour chaque bande : écart à la cible → action (creuser / renforcer / OK), plage Hz
    et ~dB indicatif. Les gestes sont DOUX (bornés) et à valider à l'oreille."""
    be = _band_energy(analyse)
    if be is None:
        return {"error": "band_energy manquant dans l'analyse (spectre non calculé — "
                "scipy absent ou audio trop court)."}
    nom_style, ref = _resoudre_style(style)
    mouvements = []
    for b in _ORDRE:
        cur, cib = be[b], ref[b]
        delta = cur - cib
        if abs(delta) <= _TOL:
            action = "OK"
        elif delta > 0:
            action = "creuser"
        else:
            action = "renforcer"
        mouvements.append({
            "bande": b, "plage_hz": _BANDES_HZ[b],
            "mesure": round(cur, 3), "cible": round(cib, 3),
            "ecart": round(delta, 3), "db_indicatif": _db_indicatif(cur, cib),
            "action": action,
        })
    prioritaires = sorted(
        (m for m in mouvements if m["action"] != "OK"),
        key=lambda m: abs(m["ecart"]), reverse=True,
    )
    return {
        "style": nom_style, "mouvements": mouvements,
        "prioritaires": [m["bande"] for m in prioritaires[:2]],
        "note": "Écarts vs courbe de RÉFÉRENCE (heuristique, point de départ). "
        "Corrige d'abord les bandes prioritaires, en gestes doux, puis réécoute. "
        "Une bosse large et douce sonne mieux qu'un boost étroit.",
    }


def detecter_masquage(analyse_lead: dict, analyse_accomp: dict, seuil: float = 0.12) -> dict:
    """Repère les bandes où DEUX éléments se masquent (énergie forte des deux côtés).

    `analyse_lead` = élément à privilégier (ex. voix), `analyse_accomp` = accompagnement
    (ex. instru). Une bande est « à risque » si les deux y ont une énergie ≥ `seuil` :
    l'accompagnement couvre le lead. Conseil : creuser LÉGÈREMENT cette bande sur
    l'accompagnement pour dégager le lead (EQ soustractif / ducking)."""
    a = _band_energy(analyse_lead)
    b = _band_energy(analyse_accomp)
    if a is None or b is None:
        return {"error": "band_energy manquant sur l'un des deux éléments (spectre non calculé)."}
    risques = []
    for bande in _ORDRE:
        if a[bande] >= seuil and b[bande] >= seuil:
            severite = round(min(a[bande], b[bande]), 3)
            risques.append({
                "bande": bande, "plage_hz": _BANDES_HZ[bande],
                "energie_lead": round(a[bande], 3),
                "energie_accomp": round(b[bande], 3),
                "severite": severite,
                "conseil": f"creuser légèrement {_BANDES_HZ[bande]} sur l'accompagnement "
                "(ou ducking piloté par le lead) pour dégager l'élément principal",
            })
    risques.sort(key=lambda r: r["severite"], reverse=True)
    return {
        "seuil": seuil, "risques": risques,
        "bande_la_plus_masquee": risques[0]["bande"] if risques else None,
        "note": "Masquage = deux sources fortes dans la même bande ; l'oreille perd le "
        "détail. Dégage le lead par EQ soustractif doux sur l'accompagnement, panning, "
        "ou ducking — plutôt que de monter le lead (qui empile l'énergie)."
        if risques else "Aucun chevauchement fort détecté : les deux éléments cohabitent bien.",
    }


def analyser_balance_tonale(analyse: dict, style: str = "neutre") -> dict:
    """Note la balance tonale globale vs une courbe de référence (0..1) + verdict/bande.

    Score = 1 − (somme des écarts absolus)/2 : 1.0 = collé à la référence, 0 = à
    l'opposé. Par bande : 'trop', 'ok' ou 'pas assez'. Heuristique de dégrossissage."""
    be = _band_energy(analyse)
    if be is None:
        return {"error": "band_energy manquant dans l'analyse (spectre non calculé)."}
    nom_style, ref = _resoudre_style(style)
    l1 = sum(abs(be[b] - ref[b]) for b in _ORDRE)
    score = round(max(0.0, 1.0 - l1 / 2.0), 3)  # L1 borné à 2 pour deux distributions
    bandes = {}
    for b in _ORDRE:
        delta = be[b] - ref[b]
        bandes[b] = "ok" if abs(delta) <= _TOL else ("trop" if delta > 0 else "pas assez")
    if score >= 0.9:
        verdict = "équilibrée — proche de la référence"
    elif score >= 0.75:
        verdict = "correcte — quelques bandes à ajuster"
    else:
        verdict = "déséquilibrée — retravailler le spectre"
    return {
        "style": nom_style, "score": score, "verdict": verdict,
        "par_bande": bandes,
        "note": "Score de PROXIMITÉ à une courbe heuristique — un repère, pas une note "
        "de qualité. Un mix peut sonner très bien loin de la référence selon l'intention.",
    }


# --------------------------------------------------------------------------- #
# Compression                                                                 #
# --------------------------------------------------------------------------- #

# Attaque/relâchement de DÉPART par source (ms). Points de départ conventionnels.
_COMP_SOURCE = {
    "voix": {"attack_ms": 8, "release_ms": 120, "knee": "douce",
             "note": "attaque moyenne : garde les consonnes, dompte les tenues"},
    "mix": {"attack_ms": 30, "release_ms": 150, "knee": "douce",
            "note": "bus mix : compression collante, peu de réduction (glue)"},
    "bus": {"attack_ms": 30, "release_ms": 150, "knee": "douce",
            "note": "bus de groupe : colle sans écraser les transitoires"},
    "basse": {"attack_ms": 15, "release_ms": 90, "knee": "douce",
              "note": "tient le niveau ; attaque pas trop rapide pour garder le punch"},
    "batterie": {"attack_ms": 10, "release_ms": 80, "knee": "dure",
                 "note": "attaque plus lente = plus de punch ; release calé au tempo"},
}


def recommander_compression(analyse: dict, source: str = "mix") -> dict:
    """Recommande des réglages de compression à partir de la DYNAMIQUE mesurée.

    Utilise le crest factor (peak − rms) : plus il est haut, plus le signal est
    dynamique et gagne à être compressé. Déduit ratio + réduction cible + seuil de
    départ ; attaque/relâchement viennent de la `source`. Points de départ à affiner."""
    if not isinstance(analyse, dict):
        return {"error": "analyse invalide."}
    crest = analyse.get("crest_factor_db")
    rms = analyse.get("rms_dbfs")
    if not isinstance(crest, (int, float)) or not isinstance(rms, (int, float)):
        return {"error": "crest_factor_db / rms_dbfs manquants (analyse audio incomplète)."}
    src = (source or "mix").strip().lower()
    if src not in _COMP_SOURCE:
        src = "mix"
    tempo = _COMP_SOURCE[src]

    # Ratio + réduction cible selon la dynamique (crest en dB).
    if crest >= 18:
        ratio, reduction, dyn = 4.0, "4–6 dB", "très dynamique"
    elif crest >= 12:
        ratio, reduction, dyn = 3.0, "3–5 dB", "dynamique"
    elif crest >= 8:
        ratio, reduction, dyn = 2.0, "2–3 dB", "modérée"
    else:
        ratio, reduction, dyn = 1.5, "1–2 dB (ou rien)", "déjà dense"
    # Seuil de départ : quelques dB sous le pic moyen (≈ rms + un tiers du crest),
    # pour n'attraper que le haut de la dynamique. Indicatif.
    seuil = round(rms + max(2.0, crest / 3.0), 1)

    conseils = []
    if crest < 8:
        conseils.append("dynamique déjà faible : peu ou pas de compression, sinon ça pompe.")
    if src == "batterie":
        conseils.append("cale le release sur le tempo pour un 'pompage' musical ; essaie la compression parallèle.")
    if src == "voix":
        conseils.append("en série léger (3:1) + un 2e comp lent pour le niveau, plutôt qu'un seul fort.")

    return {
        "source": src, "dynamique": dyn,
        "crest_factor_db": round(float(crest), 1),
        "ratio": ratio,
        "reduction_cible_db": reduction,
        "seuil_depart_dbfs": seuil,
        "attack_ms": tempo["attack_ms"], "release_ms": tempo["release_ms"],
        "knee": tempo["knee"],
        "note": tempo["note"],
        "conseils": conseils,
        "note_methode": "Réglages DÉRIVÉS du crest factor mesuré — points de départ. "
        "Règle le seuil pour viser la réduction cible au VU, puis affine à l'oreille.",
    }


# --------------------------------------------------------------------------- #
# Saturation                                                                  #
# --------------------------------------------------------------------------- #


def recommander_saturation(analyse: dict, style: str = "neutre") -> dict:
    """Recommande un type et un dosage de saturation à partir du spectre + dynamique.

    Un signal terne (peu de présence/air) gagne à une saturation qui génère des
    harmoniques ; un signal déjà brillant en a moins besoin. Le crest oriente le
    dosage (matériau dynamique = encaisse plus de drive). Points de départ."""
    be = _band_energy(analyse)
    if be is None:
        return {"error": "band_energy manquant dans l'analyse (spectre non calculé)."}
    crest = analyse.get("crest_factor_db")
    haut = be["presence"] + be["air"]      # énergie du haut du spectre
    bas = be["sub"] + be["low"]

    # Type + cible selon le profil spectral. Le cas « bas dominant » est le plus
    # spécifique : on le teste AVANT la ternité (un morceau très basseux appelle
    # d'abord un travail des basses, même s'il manque aussi d'aigus).
    if bas > 0.52:
        type_sat, cible, pourquoi = "bande (tape)", "bas 120–500 Hz (parallèle)", \
            "bas dominant : la bande colle et arrondit sans boueux"
    elif haut < 0.20:
        type_sat, cible, pourquoi = "lampe (tube)", "présence 2–6 kHz", \
            "spectre terne : les harmoniques paires réchauffent et ouvrent le haut"
    else:
        type_sat, cible, pourquoi = "bande (tape)", "bus global (léger)", \
            "profil équilibré : une bande douce en bus pour la cohésion"

    # Dosage selon la dynamique.
    if isinstance(crest, (int, float)) and crest >= 14:
        drive_pct, dose = 30, "matériau dynamique : peut encaisser un drive marqué"
    elif isinstance(crest, (int, float)) and crest < 8:
        drive_pct, dose = 10, "déjà dense : drive léger sinon ça devient dur/fatigant"
    else:
        drive_pct, dose = 20, "drive modéré"

    mode = "parallèle" if "parallèle" in cible else "série"
    return {
        "style": _resoudre_style(style)[0],
        "type": type_sat, "cible": cible, "mode": mode,
        "drive_pct": drive_pct, "dosage_note": dose, "pourquoi": pourquoi,
        "conseils": [
            "compense le volume après saturation (elle monte le niveau perçu — compare à niveau égal).",
            "en parallèle : sature une copie et dose au fader, l'original reste propre.",
        ],
        "note_methode": "Type/dosage DÉRIVÉS du spectre et du crest mesurés — points de "
        "départ. La saturation ajoute des harmoniques : vérifie qu'elle n'agresse pas les aigus.",
    }
