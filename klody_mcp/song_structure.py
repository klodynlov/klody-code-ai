"""Contrôle de couverture des paroles AVANT de lancer une génération de chanson.

Problème résolu : le moteur (ACE-Step, via le daemon local-suno) « saute des
sections » ou « répète le refrain au lieu de couvrir tout le texte ». Ce n'est pas
un caprice du modèle — ce sont **trois** conditions déterministes, décidées par ce
que Klody ENVOIE, et toutes vérifiables avant de dépenser une génération :

1. **Trop de mots pour la durée.** Le daemon vise ~2 mots chantés par seconde
   (``local-suno/main.py::_warn_if_lyrics_too_short``, qui l'écrit en toutes
   lettres). Mesuré sur les 78 chansons chantées de ``library.db`` : des demandes
   à 15,3 · 6,0 · 5,7 · 5,6 · 5,3 mots/s — soit jusqu'à 7× la cible. À ce débit le
   moteur ne peut PAS tout chanter : il coupe. Le défaut ``duree_sec=30`` de
   ``generer_chanson`` avec des paroles complètes produisait exactement ça.

2. **Pas assez de sections pour les segments.** Au-delà de
   ``ACESTEP_MAX_SEGMENT_SEC`` (120 s), le daemon génère la chanson en N segments
   recollés et répartit l'arrangement entre eux (``split_arrangement_text``). S'il
   y a MOINS de sections que de segments, ``generate_song_long`` fait
   ``chunks.append(chunks[-1])`` : **les segments de fin re-chantent le même
   texte**. Un texte sans ligne vide ni en-tête = 1 seule section = tous les
   segments identiques. Mesuré : 17 des 78 chansons n'avaient qu'une section.

3. **Rôles de section perdus.** ``_build_lyrics_from_custom`` nomme
   ``section_2``, ``section_3``… tout bloc séparé par une simple ligne vide, et
   ``section_marker`` renvoie ``[verse]`` pour ces noms inconnus. Le refrain
   n'est alors plus balisé comme un refrain — le moteur n'a plus de structure à
   suivre. Mesuré : 10 des 78 chansons avaient au moins une clé ``section_N``.
   ⚠️ Et deux blocs balisés ``[Refrain]`` s'écrasent l'un l'autre : le parseur du
   daemon range les sections dans un **dict** dont la clé est le nom de l'en-tête.
   D'où la NUMÉROTATION des marqueurs émis ici (``[chorus 1]``, ``[chorus 2]``) :
   sans elle, canoniser les marqueurs ferait perdre des couplets.

Ce module ne parle à personne : il calcule. Les serveurs MCP s'en servent pour
refuser AVANT d'envoyer (une génération de 3 minutes se découvre à l'écoute, pas
dans un code retour) et pour déduire une durée cohérente quand l'appelant n'en
donne pas.

⚠️ Les constantes ci-dessous DUPLIQUENT le contrat d'un autre dépôt
(``~/local-suno``), qui n'est pas une dépendance de celui-ci — le lien est HTTP.
Une constante recopiée est une constante qui dérive : ``_idee_to_body`` bornait la
durée à 120 s en citant « bornes daemon (ge=10 le=120) » alors que le daemon était
passé à 600 depuis. ``tests/test_song_structure.py`` relit donc les vraies valeurs
dans ``~/local-suno`` quand il est présent sur la machine, et rougit si elles ont
bougé. Absent, le test se saute — il ne peut pas juger, il le dit.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata

# ── Contrat du daemon local-suno (valeurs recopiées, cf. l'avertissement ci-dessus)

# storage/models.py::GenerationRequest.duration_sec — Field(ge=10, le=600)
DUREE_MIN_SEC = int(os.getenv("KLODY_SONG_DUREE_MIN", "10"))
DUREE_MAX_SEC = int(os.getenv("KLODY_SONG_DUREE_MAX", "600"))
# storage/models.py::GenerationRequest.bpm — Field(ge=60, le=180)
BPM_MIN, BPM_MAX = 60, 180
# config.py — ACESTEP_MAX_SEGMENT_SEC / ACESTEP_SEGMENT_OVERLAP_SEC
SEGMENT_MAX_SEC = float(os.getenv("ACESTEP_MAX_SEGMENT_SEC", "120"))
SEGMENT_OVERLAP_SEC = float(os.getenv("ACESTEP_SEGMENT_OVERLAP_SEC", "4"))

# ── Débit de chant ────────────────────────────────────────────────────────────
# La cible vient du daemon lui-même (main.py::_warn_if_lyrics_too_short : « vise
# ~2 mots/s »), pas d'une estimation maison. Les deux bornes l'encadrent :
#   - sous DEBIT_MIN, c'est le seuil où le daemon avertit déjà « chant étiré et
#     peu intelligible » — sauf que son avertissement est un print dans un
#     sous-processus worker : ni l'utilisateur ni Klody ne le voient jamais ;
#   - au-dessus de DEBIT_MAX (cible + 50 %), le moteur doit couper du texte.
#     Volontairement permissif : on ne refuse que le flagrant, pas le serré.
DEBIT_CIBLE = float(os.getenv("KLODY_SONG_DEBIT_CIBLE", "2.0"))
DEBIT_MIN = float(os.getenv("KLODY_SONG_DEBIT_MIN", "1.0"))
DEBIT_MAX = float(os.getenv("KLODY_SONG_DEBIT_MAX", "3.0"))


# ── Balises de section ────────────────────────────────────────────────────────
# Miroir de local-suno/pipeline/song_format.py::_SECTION_PREFIXES. L'ordre compte
# ici (contrairement à la source) : « prerefrain » doit être testé AVANT
# « refrain », sinon un pré-refrain devient un refrain.
_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("intro",), "intro"),
    (("prechorus", "pre-chorus", "pre_chorus", "pre chorus", "prerefrain",
      "pre-refrain", "pre_refrain", "pre refrain"), "prechorus"),
    (("verse", "couplet"), "verse"),
    (("chorus", "refrain", "hook"), "chorus"),
    (("bridge", "pont"), "bridge"),
    (("outro", "coda", "fin"), "outro"),
)

_ROLE_DEFAUT = "verse"

# En-tête entre crochets : « [Couplet 1] », « [Refrain] », « [Chorus 2] ».
_ENTETE_CROCHETS = re.compile(r"^\[\s*(.+?)\s*\]\s*$")
# En-tête nu, tel qu'un LLM l'écrit hors convention Suno : « Couplet 1 : »,
# « REFRAIN », « Pont ». Borné aux mots de section connus pour ne jamais avaler
# un vers qui commencerait par un de ces mots — d'où le `$` et l'absence de
# ponctuation autre que « : ».
# ⚠️ « fin » et « coda » sont ABSENTS de cette liste alors qu'ils balisent bien un
# outro entre crochets : nus, ce sont d'abord des paroles plausibles (« Fin. »).
# Un en-tête manqué coûte une section ; un vers avalé comme en-tête PERD le vers.
_MOTS_SECTION = (
    "intro", "couplet", "verse", "refrain", "chorus", "hook", "pont", "bridge",
    "outro", "prechorus", "pre-chorus", "pre chorus", "prerefrain",
    "pre-refrain", "pre refrain",
)
_ENTETE_NUE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(m) for m in _MOTS_SECTION) + r")"
    r"(?:\s*[-–—]?\s*\d+)?\s*:?\s*$",
    re.IGNORECASE,
)


def _sans_accents(s: str) -> str:
    """« pré-refrain » → « pre-refrain » : les rôles se comparent sans accents."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def role_canonique(nom: str) -> str:
    """Rôle canonique d'un nom de section (``"Couplet 1"`` → ``"verse"``).

    Un nom inconnu retombe sur ``verse`` — même choix que le daemon
    (``section_marker``), pour la même raison : mieux vaut un couplet qu'aucune
    balise. L'appelant est prévenu par le rapport, pas par une exception.
    """
    k = _sans_accents(nom.strip().lower())
    for prefixes, role in _PREFIXES:
        if k.startswith(prefixes):
            return role
    return _ROLE_DEFAUT


def _est_entete(ligne: str) -> str | None:
    """Nom de section si la ligne est un en-tête, sinon None."""
    m = _ENTETE_CROCHETS.match(ligne)
    if m:
        return m.group(1)
    nue = ligne.strip()
    if nue and _ENTETE_NUE.match(_sans_accents(nue)):
        return nue.rstrip(" :")
    return None


def decouper_sections(paroles: str) -> list[tuple[str | None, str]]:
    """Découpe un texte de paroles en blocs ``(nom_de_section | None, texte)``.

    Un bloc commence à chaque en-tête (``[Refrain]``, ``Couplet 2 :``) ou après
    une ligne vide. ``None`` = bloc sans en-tête : son rôle est INCONNU, pas
    « couplet ». La distinction est le cœur du rapport — deviner le rôle d'un
    bloc serait transformer les paroles sans le dire.
    """
    blocs: list[tuple[str | None, str]] = []
    nom: str | None = None
    tampon: list[str] = []

    def _vider() -> None:
        # Un en-tête sans texte ne produit RIEN : le daemon saute lui aussi les
        # sections vides (`_arrangement_blocks`), donc en compter une ici rendrait
        # un décompte de sections plus optimiste que ce qui sera chanté — et c'est
        # ce décompte qui décide si les segments peuvent se partager le texte.
        nonlocal nom, tampon
        texte = "\n".join(tampon).strip()
        if texte:
            blocs.append((nom, texte))
        nom, tampon = None, []

    for ligne in (paroles or "").splitlines():
        entete = _est_entete(ligne)
        if entete is not None:
            _vider()
            nom = entete
        elif not ligne.strip():
            if tampon:
                _vider()
        else:
            tampon.append(ligne)
    _vider()
    return blocs


def canonicaliser_paroles(paroles: str) -> tuple[str, dict]:
    """Réécrit les paroles en arrangement balisé, prêt pour le daemon.

    Chaque bloc reçoit une balise canonique NUMÉROTÉE (``[verse 1]``,
    ``[chorus 1]``, ``[verse 2]``…) et les blocs sont séparés par une ligne vide.

    La numérotation n'est pas cosmétique : le parseur du daemon
    (``_build_lyrics_from_custom``) range les sections dans un dict indexé par le
    nom d'en-tête, donc deux ``[chorus]`` s'écraseraient et un couplet
    disparaîtrait. Le suffixe numérique est ensuite retiré côté daemon par
    ``_base_section_name`` avant le mapping vers ``[chorus]`` : la balise finale
    envoyée au moteur reste canonique.

    Returns:
        ``(arrangement, rapport)`` — rapport :
        ``{"sections", "roles", "sans_etiquette", "renommees", "deja_canonique"}``.
    """
    blocs = decouper_sections(paroles)
    if not blocs:
        return "", {
            "sections": 0, "roles": [], "sans_etiquette": 0,
            "renommees": [], "deja_canonique": True,
        }

    compteurs: dict[str, int] = {}
    lignes: list[str] = []
    roles: list[str] = []
    renommees: list[str] = []
    sans_etiquette = 0

    for nom, texte in blocs:
        if nom is None:
            sans_etiquette += 1
            role = _ROLE_DEFAUT
        else:
            role = role_canonique(nom)
            if _sans_accents(nom.strip().lower()) != role:
                renommees.append(f"{nom} → [{role}]")
        compteurs[role] = compteurs.get(role, 0) + 1
        roles.append(role)
        lignes.append(f"[{role} {compteurs[role]}]\n{texte}")

    arrangement = "\n\n".join(lignes)
    return arrangement, {
        "sections": len(blocs),
        "roles": roles,
        "sans_etiquette": sans_etiquette,
        "renommees": renommees,
        "deja_canonique": not renommees and sans_etiquette == 0,
    }


def mots_chantes(arrangement: str) -> int:
    """Nombre de mots réellement chantés (les lignes de balise n'en sont pas).

    Même règle de comptage que ``local-suno/main.py::_warn_if_lyrics_too_short``,
    pour que le débit calculé ici soit le même nombre que celui journalisé là-bas.
    """
    return sum(
        len(ligne.split())
        for ligne in (arrangement or "").splitlines()
        if not ligne.strip().startswith("[")
    )


def nb_segments(duree_sec: float) -> int:
    """Nombre de segments que le daemon générera pour cette durée.

    Réplique exacte de ``plan_segment_durations`` : le chevauchement peut pousser
    un segment au-dessus du plafond, ce qui en ajoute un — reproduire la boucle
    plutôt que le seul ``ceil`` évite de sous-estimer d'un segment à la charnière.
    """
    if duree_sec <= SEGMENT_MAX_SEC:
        return 1
    n = math.ceil(duree_sec / SEGMENT_MAX_SEC)
    seg = (duree_sec + (n - 1) * SEGMENT_OVERLAP_SEC) / n
    while seg > SEGMENT_MAX_SEC and n < 1000:
        n += 1
        seg = (duree_sec + (n - 1) * SEGMENT_OVERLAP_SEC) / n
    return n


def duree_conseillee(mots: int) -> int:
    """Durée qui fait tomber le débit GLOBAL sur la cible du daemon (~2 mots/s).

    ⚠️ Plancher, pas verdict : au-delà d'un segment, c'est le débit du segment le
    plus dense qui décide (cf. ``debit_par_segment``). Utiliser ce seul chiffre
    laisserait passer une chanson dont un segment est saturé — mesuré in vivo.
    """
    if mots <= 0:
        return DUREE_MIN_SEC
    return max(DUREE_MIN_SEC, min(round(mots / DEBIT_CIBLE), DUREE_MAX_SEC))


def _groupes_equilibres(n_items: int, k: int) -> list[tuple[int, int]]:
    """Réplique de ``_balanced_groups`` : k groupes CONTIGUS de tailles ~égales.

    Renvoie les bornes ``(début, fin)`` de chaque groupe. ⚠️ L'équilibrage porte
    sur le NOMBRE de sections, pas sur leur longueur — c'est précisément ce qui
    crée des segments de densités très différentes.
    """
    k = max(1, min(k, n_items))
    base, extra = divmod(n_items, k)
    bornes: list[tuple[int, int]] = []
    debut = 0
    for i in range(k):
        taille = base + (1 if i < extra else 0)
        bornes.append((debut, debut + taille))
        debut += taille
    return bornes


def debit_par_segment(arrangement: str, duree_sec: float) -> list[tuple[int, float]]:
    """``(mots, mots/s)`` de chaque segment, tels que le daemon les répartira.

    Le daemon découpe l'arrangement en groupes de sections contigus de tailles
    égales EN NOMBRE (``split_arrangement_text`` → ``_balanced_groups``), puis
    donne à chacun un segment de durée identique (``plan_segment_durations``
    renvoie des durées égales). Deux sections courtes et deux longues dans le même
    morceau produisent donc deux segments de débits très différents.

    Mesuré in vivo le 2026-08-07 (session ``be133ea1``, 358 mots sur 180 s) :
    débit global 1,99 mots/s — conforme — mais **2,52** sur le segment 1 (5
    sections, 232 mots) contre 1,37 sur le segment 2. Le segment 1 a tronqué (un
    couplet amputé de 5 vers sur 8, un pré-refrain réduit à des onomatopées), le
    segment 2 est sorti intégral. Le débit global masquait exactement le seul
    chiffre qui décidait.
    """
    blocs = [b for b in arrangement.split("\n\n") if b.strip()]
    if not blocs:
        return []
    n = nb_segments(duree_sec)
    # Durée d'un segment : le daemon les fait égales, chevauchement compris.
    duree_segment = duree_sec if n == 1 else (duree_sec + (n - 1) * SEGMENT_OVERLAP_SEC) / n
    sortie: list[tuple[int, float]] = []
    for debut, fin in _groupes_equilibres(len(blocs), n):
        mots = mots_chantes("\n".join(blocs[debut:fin]))
        sortie.append((mots, mots / duree_segment if duree_segment else 0.0))
    return sortie


def duree_sans_saturation(arrangement: str) -> int:
    """Plus courte durée où AUCUN segment ne dépasse la cible de débit.

    Part du plancher global (mots ÷ cible) et allonge tant qu'un segment sature.
    Le balayage est nécessaire parce que la relation n'est pas monotone : allonger
    la durée peut ajouter un segment, donc redécouper les sections autrement.
    """
    mots = mots_chantes(arrangement)
    if mots <= 0:
        return DUREE_MIN_SEC
    depart = duree_conseillee(mots)
    for duree in range(depart, DUREE_MAX_SEC + 1):
        debits = debit_par_segment(arrangement, duree)
        if not debits or max(d for _, d in debits) <= DEBIT_CIBLE:
            return duree
    return DUREE_MAX_SEC


def sections_minimum(duree_sec: float) -> int:
    """Sections nécessaires pour que chaque segment chante un texte DIFFÉRENT."""
    return nb_segments(duree_sec)


def controler_couverture(paroles: str, duree_sec: int | None = None) -> dict:
    """Vérifie qu'une génération peut couvrir TOUT le texte, avant de la lancer.

    Args:
        paroles: paroles brutes (telles que l'appelant les a écrites).
        duree_sec: durée demandée. ``None`` = à déduire des paroles.

    Returns:
        Un rapport complet, toujours de la même forme :
        ``{"arrangement", "mots", "sections", "duree_sec", "duree_conseillee_sec",
        "segments", "sections_min", "debit_mots_s", "couvrable", "problemes",
        "avertissements", "structure": {...}}``.

        ``couvrable=False`` signale un rendu TRONQUÉ ou RÉPÉTÉ garanti, pas un
        risque : chaque entrée de ``problemes`` nomme le mécanisme et le remède.
    """
    arrangement, structure = canonicaliser_paroles(paroles)
    mots = mots_chantes(arrangement)
    conseillee = duree_sans_saturation(arrangement)

    duree_demandee = duree_sec
    duree = conseillee if duree_sec is None else int(duree_sec)
    duree = max(DUREE_MIN_SEC, min(duree, DUREE_MAX_SEC))

    segments = nb_segments(duree)
    besoin = sections_minimum(duree)
    debit = (mots / duree) if duree else 0.0
    par_segment = debit_par_segment(arrangement, duree)
    # Le chiffre qui décide vraiment : le segment le plus dense. Le débit GLOBAL
    # peut être conforme pendant qu'un segment sature et tronque (mesuré in vivo).
    debit_pire = max((d for _, d in par_segment), default=debit)

    problemes: list[str] = []
    avertissements: list[str] = []

    if mots == 0:
        problemes.append("aucune parole à chanter.")

    # (1) Trop de mots pour la durée → le moteur coupe.
    elif debit_pire > DEBIT_MAX:
        ou = "" if segments == 1 else " sur le segment le plus dense"
        problemes.append(
            f"{mots} mots sur {duree} s = {debit_pire:.1f} mots/s{ou}, soit "
            f"{debit_pire / DEBIT_CIBLE:.1f}× la cible de {DEBIT_CIBLE:g} mots/s du "
            f"moteur : il ne pourra pas tout chanter et coupera des sections. "
            f"Durée cohérente pour ce texte : {conseillee} s."
        )
    elif debit_pire > DEBIT_CIBLE:
        # ⚠️ Seuil à la CIBLE, pas au-dessus : mesuré, un segment à 2,52 mots/s a
        # tronqué (session be133ea1). C'était sous l'ancien seuil d'alerte à 2,5.
        detail = (
            "" if segments == 1
            else f" — les segments sont équilibrés en NOMBRE de sections, pas en mots"
            f" ({' / '.join(f'{m} mots' for m, _ in par_segment)})"
        )
        avertissements.append(
            f"débit serré : {debit_pire:.1f} mots/s"
            + ("" if segments == 1 else " sur le segment le plus dense")
            + f" pour une cible de {DEBIT_CIBLE:g}{detail}. "
            f"{conseillee} s laisserait respirer le texte."
        )
    elif mots and debit < DEBIT_MIN:
        avertissements.append(
            f"texte clairsemé ({debit:.1f} mots/s) : le moteur étirera les "
            f"syllabes et le chant sera peu intelligible. "
            f"{conseillee} s conviendrait mieux à {mots} mots."
        )

    # (2) Moins de sections que de segments → les derniers segments se répètent.
    if segments > 1 and structure["sections"] < besoin:
        problemes.append(
            f"{duree} s = {segments} segments générés séparément, mais le texte "
            f"n'a que {structure['sections']} section(s) : les "
            f"{segments - structure['sections']} derniers segments RE-CHANTERONT "
            f"le même texte. Découpe les paroles en au moins {besoin} sections "
            f"(une ligne vide ou un en-tête [Couplet]/[Refrain] entre chaque)."
        )

    # (3) Rôles non étiquetés : le moteur perd la structure (tout devient couplet).
    if structure["sans_etiquette"]:
        avertissements.append(
            f"{structure['sans_etiquette']} bloc(s) sans en-tête, balisés "
            f"[verse] par défaut — un refrain non balisé ne sera pas traité "
            f"comme un refrain. Ajoute [Couplet]/[Refrain]/[Pont]."
        )

    return {
        "arrangement": arrangement,
        "mots": mots,
        "sections": structure["sections"],
        "duree_sec": duree,
        "duree_demandee_sec": duree_demandee,
        "duree_conseillee_sec": conseillee,
        "segments": segments,
        "sections_min": besoin,
        "debit_mots_s": round(debit, 2),
        "debit_pire_segment": round(debit_pire, 2),
        "mots_par_segment": [m for m, _ in par_segment],
        "couvrable": not problemes,
        "problemes": problemes,
        "avertissements": avertissements,
        "structure": structure,
    }


def message_de_refus(rapport: dict) -> str:
    """Message d'erreur actionnable pour un rapport non couvrable."""
    return (
        "génération refusée — le texte ne serait pas couvert en entier : "
        + " ".join(rapport["problemes"])
        + " (passe forcer=True pour lancer quand même.)"
    )
