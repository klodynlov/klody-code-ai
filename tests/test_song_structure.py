"""Tests de klody_mcp.song_structure — le contrôle de couverture des paroles.

Ce module décide si une génération de chanson pourra chanter TOUT le texte. Ses
deux verdicts négatifs correspondent à deux mécanismes mesurés dans le daemon
local-suno, pas à des heuristiques de prudence :

- trop de mots pour la durée ⇒ le moteur coupe ;
- moins de sections que de segments ⇒ ``generate_song_long`` recopie la dernière
  section dans les segments restants (ils re-chantent la même chose).

Le second mécanisme n'existe que si le daemon DÉCOUPE : depuis le 2026-09-09,
ACE-Step v1.5 (moteur par défaut) chante en une passe jusqu'à 600 s, et seul v1
(ou une surcharge explicite) découpe encore à 120 s. Les tests de découpage se
placent donc explicitement dans ce régime (`moteur_v1`) ; leurs jumeaux
`moteur_v15` vérifient que le régime par défaut ne refuse pas à tort.

La dernière classe (`TestPasDeDerive`) relit les vraies constantes de
``~/local-suno`` et rejoue le circuit complet à travers SON parseur, dans les deux
régimes. Elle se saute si le dépôt est absent de la machine — elle ne peut alors
pas juger, et le dit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from klody_mcp import song_structure as ss

_LOCALSUNO = Path.home() / "local-suno"
_RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture
def moteur_v1(monkeypatch):
    """Régime DÉCOUPÉ : plafond de segment historique de v1 (120 s).

    C'est aussi celui de ``ACESTEP_MAX_SEGMENT_SEC=120`` sous v1.5. Littéral voulu :
    le tirer de `ss` ferait suivre au test la valeur qu'il doit juger.
    """
    monkeypatch.setattr(ss, "SEGMENT_MAX_SEC", 120.0)


@pytest.fixture
def moteur_v15(monkeypatch):
    """Régime PAR DÉFAUT du daemon : une passe jusqu'à 600 s, indépendamment du shell."""
    monkeypatch.setattr(ss, "SEGMENT_MAX_SEC", 600.0)


# --------------------------------------------------------------------------- #
# Canonicalisation des marqueurs                                               #
# --------------------------------------------------------------------------- #


class TestCanonicalisation:
    def test_entetes_entre_crochets(self):
        arr, rap = ss.canonicaliser_paroles(
            "[Couplet 1]\nun deux\n\n[Refrain]\ntrois quatre\n\n[Pont]\ncinq"
        )
        assert arr.splitlines()[0] == "[verse 1]"
        assert rap["roles"] == ["verse", "chorus", "bridge"]
        assert rap["sans_etiquette"] == 0

    def test_entetes_nus(self):
        # Un LLM écrit souvent hors convention Suno : « Refrain : », « Pont ».
        _, rap = ss.canonicaliser_paroles(
            "Couplet 1 :\nun deux\n\nRefrain\ntrois\n\nPONT\nquatre"
        )
        assert rap["roles"] == ["verse", "chorus", "bridge"]

    def test_accents_et_prerefrain(self):
        # « pré-refrain » doit devenir prechorus, PAS chorus : l'ordre des préfixes
        # est significatif ici, contrairement à la table source du daemon.
        _, rap = ss.canonicaliser_paroles("[Pré-refrain]\nun\n\n[Refrain]\ndeux")
        assert rap["roles"] == ["prechorus", "chorus"]

    def test_numerotation_distincte_par_role(self):
        # Le parseur du daemon range les sections dans un DICT indexé par le nom
        # d'en-tête : deux « [chorus] » s'écraseraient et un refrain disparaîtrait.
        arr, _ = ss.canonicaliser_paroles(
            "[Refrain]\nun\n\n[Couplet]\ndeux\n\n[Refrain]\ntrois"
        )
        entetes = [x for x in arr.splitlines() if x.startswith("[")]
        assert entetes == ["[chorus 1]", "[verse 1]", "[chorus 2]"]
        assert len(set(entetes)) == 3, "des en-têtes identiques s'écraseraient"

    def test_blocs_sans_entete_sont_comptes_pas_devines(self):
        # Deviner qu'un bloc est un refrain serait transformer les paroles sans le
        # dire. On balise [verse] comme le daemon, et on SIGNALE.
        _, rap = ss.canonicaliser_paroles("un deux\n\ntrois quatre")
        assert rap["sections"] == 2
        assert rap["sans_etiquette"] == 2
        assert rap["roles"] == ["verse", "verse"]

    def test_un_vers_nest_pas_avale_comme_entete(self):
        # « Fin » nu est volontairement absent de la table des en-têtes nus : c'est
        # d'abord une parole plausible. Un vers avalé comme en-tête est PERDU.
        arr, rap = ss.canonicaliser_paroles("[Outro]\nFin\nvoilà")
        assert rap["sections"] == 1
        assert "Fin" in arr and "voilà" in arr
        assert ss.mots_chantes(arr) == 2

    def test_entete_inconnu_devient_couplet_et_est_signale(self):
        # « [Ad-lib] », « [Solo] »… : rôle inconnu. Même repli que le daemon
        # (`section_marker` → [verse]), mais le rapport le NOMME — sinon l'appelant
        # croirait que sa balise a été comprise.
        arr, rap = ss.canonicaliser_paroles("[Ad-lib]\nouh ouh\n\n[Refrain]\nun deux")
        assert rap["roles"] == ["verse", "chorus"]
        assert "Ad-lib → [verse]" in rap["renommees"]
        assert arr.startswith("[verse 1]")

    def test_texte_vide(self):
        arr, rap = ss.canonicaliser_paroles("   \n\n  ")
        assert arr == "" and rap["sections"] == 0

    def test_entete_sans_texte_ne_cree_pas_de_section_vide(self):
        # Le daemon saute les sections sans texte : en émettre une créerait un
        # décompte de sections plus optimiste que la réalité.
        _, rap = ss.canonicaliser_paroles("[Intro]\n\n[Couplet]\nun deux")
        assert rap["sections"] == 1


class TestComptage:
    def test_les_balises_ne_sont_pas_des_mots(self):
        # Même règle que local-suno/main.py::_warn_if_lyrics_too_short, pour que
        # le débit calculé ici soit le même nombre que celui journalisé là-bas.
        assert ss.mots_chantes("[verse 1]\nun deux trois\n\n[chorus 1]\nquatre") == 4

    def test_duree_conseillee_suit_la_cible_du_daemon(self):
        assert ss.duree_conseillee(360) == 180  # 360 mots / 2 mots·s⁻¹
        assert ss.duree_conseillee(0) == ss.DUREE_MIN_SEC
        assert ss.duree_conseillee(100_000) == ss.DUREE_MAX_SEC


class TestPlafondDeSegment:
    """Le plafond suit le moteur, exactement comme ``local-suno/config.py``.

    Littéraux verrouillés, et jugés sur le module IMPORTÉ dans un environnement
    propre : c'est l'expression de niveau module qui porte la règle (condition
    sur ``ACE_STEP_VERSION`` + surcharge prioritaire). Ce test tourne aussi là où
    local-suno est absent (CI), contrairement à `TestPasDeDerive`.
    """

    @pytest.mark.parametrize(
        ("env", "attendu"),
        [
            ({}, ("v15", 600.0)),
            ({"ACE_STEP_VERSION": "v1"}, ("v1", 120.0)),
            # La surcharge explicite gagne sur le moteur — c'est le chemin que la
            # doc du daemon donne pour rétablir le découpage en v1.5.
            ({"ACE_STEP_VERSION": "v15", "ACESTEP_MAX_SEGMENT_SEC": "120"}, ("v15", 120.0)),
        ],
    )
    def test_plafond_selon_moteur_et_surcharge(self, env, attendu):
        assert tuple(_constantes_klody(env)[k] for k in ("version", "segment_max")) == attendu


@pytest.mark.usefixtures("moteur_v1")
class TestSegments:
    @pytest.mark.parametrize(
        ("duree", "attendu"), [(30, 1), (120, 1), (121, 2), (180, 2), (240, 3), (360, 4)]
    )
    def test_nombre_de_segments(self, duree, attendu):
        assert ss.nb_segments(duree) == attendu

    def test_le_chevauchement_peut_ajouter_un_segment(self):
        # 240 s : ceil(240/120) = 2 donnerait des segments de 122 s > plafond.
        # Reproduire la seule division ferait sous-estimer d'un segment.
        assert ss.nb_segments(240) == 3


@pytest.mark.usefixtures("moteur_v15")
class TestUnePasse:
    def test_toute_duree_du_contrat_tient_en_un_segment(self):
        # Le plafond de segment égale la borne haute du contrat : v1.5 ne découpe
        # jamais une durée que le daemon accepte.
        assert {ss.nb_segments(d) for d in (30, 121, 240, 360, ss.DUREE_MAX_SEC)} == {1}

    def test_le_temoin_inegal_ne_sature_plus(self):
        # Le contre-exemple de 2026-08-07 (segment 1 à 2,52 mots/s) n'existe que
        # parce que le découpage équilibre le NOMBRE de sections. En une passe, le
        # seul débit est le débit global — conforme.
        r = ss.controler_couverture(_SECTIONS_INEGALES, 180)
        assert r["segments"] == 1
        assert r["debit_pire_segment"] == r["debit_mots_s"] <= ss.DEBIT_CIBLE
        assert r["avertissements"] == []

    def test_un_bloc_unique_de_240s_nest_plus_refuse(self):
        # En v1 : 3 segments, 1 section ⇒ refus « RE-CHANTERONT ». En une passe,
        # le texte entier est chanté une fois : refuser serait faux.
        r = ss.controler_couverture(_paroles(1, 480), 240)
        assert r["couvrable"] is True
        assert r["segments"] == r["sections_min"] == 1


# Chanson RÉELLE générée le 2026-08-07 (session be133ea1) : 9 sections de
# longueurs inégales, 358 mots. Débit global 1,99 mots/s — conforme — mais le
# segment 1 a reçu 5 sections (232 mots) et a TRONQUÉ à l'écoute, transcription
# Whisper à l'appui. C'est le contre-exemple qui a montré que le débit global ne
# suffit pas ; il sert de témoin ici.
_SECTIONS_INEGALES = "\n\n".join(
    [
        "[Couplet 1]\n" + " ".join(["mot"] * 66),
        "[Pré-refrain]\n" + " ".join(["mot"] * 20),
        "[Refrain]\n" + " ".join(["mot"] * 48),
        "[Couplet 2]\n" + " ".join(["mot"] * 66),
        "[Pré-refrain]\n" + " ".join(["mot"] * 22),
        "[Refrain]\n" + " ".join(["mot"] * 48),
        "[Pont]\n" + " ".join(["mot"] * 26),
        "[Refrain]\n" + " ".join(["mot"] * 34),
        "[Outro]\n" + " ".join(["mot"] * 8),
    ]
)


@pytest.mark.usefixtures("moteur_v1")
class TestDebitParSegment:
    def test_le_debit_global_peut_masquer_un_segment_sature(self):
        r = ss.controler_couverture(_SECTIONS_INEGALES, 180)
        assert r["segments"] == 2
        assert r["debit_mots_s"] <= ss.DEBIT_CIBLE, "le global est conforme…"
        assert r["debit_pire_segment"] > ss.DEBIT_CIBLE, "…et pourtant un segment sature"
        assert r["mots_par_segment"][0] > r["mots_par_segment"][1]

    def test_le_segment_sature_est_signale_et_explique(self):
        r = ss.controler_couverture(_SECTIONS_INEGALES, 180)
        (alerte,) = r["avertissements"]
        assert "segment le plus dense" in alerte
        # Le remède est dans le message : le déséquilibre vient du découpage par
        # NOMBRE de sections, pas de la longueur du texte.
        assert "NOMBRE de sections" in alerte
        assert str(r["duree_conseillee_sec"]) in alerte

    def test_la_duree_deduite_desature_le_pire_segment(self):
        r = ss.controler_couverture(_SECTIONS_INEGALES, None)
        assert r["debit_pire_segment"] <= ss.DEBIT_CIBLE
        assert r["avertissements"] == []
        # Plus long que le plancher global, précisément à cause du déséquilibre.
        assert r["duree_sec"] > ss.duree_conseillee(r["mots"])

    def test_un_seul_segment_na_quun_debit(self):
        r = ss.controler_couverture(_paroles(3, 40), 60)
        assert r["segments"] == 1
        assert r["debit_pire_segment"] == r["debit_mots_s"]

    def test_sections_egales_ne_paient_aucun_supplement(self):
        # Sans déséquilibre, la durée déduite reste le plancher global : le
        # nouveau critère ne doit pas rallonger tout le monde par précaution.
        paroles = _paroles(6, 60)
        r = ss.controler_couverture(paroles, None)
        assert r["duree_sec"] == ss.duree_conseillee(r["mots"])

    def test_arrangement_vide(self):
        assert ss.debit_par_segment("", 180) == []
        assert ss.duree_sans_saturation("") == ss.DUREE_MIN_SEC


# --------------------------------------------------------------------------- #
# Contrôle de couverture                                                       #
# --------------------------------------------------------------------------- #


def _paroles(n_sections: int, mots_par_section: int) -> str:
    return "\n\n".join(
        f"[Couplet {i + 1}]\n" + " ".join(["mot"] * mots_par_section)
        for i in range(n_sections)
    )


class TestCouverture:
    def test_texte_trop_dense_est_refuse(self):
        # 400 mots sur 30 s = 13 mots/s. Mesuré dans library.db : une demande
        # réelle à 15,3 mots/s (460 mots sur 30 s). Le moteur ne peut que couper.
        r = ss.controler_couverture(_paroles(4, 100), 30)
        assert r["couvrable"] is False
        assert "coupera des sections" in r["problemes"][0]
        # Le refus DIT la durée qui marcherait, sinon il n'est pas actionnable.
        assert str(r["duree_conseillee_sec"]) in r["problemes"][0]

    def test_moins_de_sections_que_de_segments_est_refuse(self, moteur_v1):
        # 240 s = 3 segments ; 2 sections ⇒ le 3ᵉ segment re-chante le 2ᵉ.
        r = ss.controler_couverture(_paroles(2, 240), 240)
        assert r["couvrable"] is False
        assert "RE-CHANTERONT" in r["problemes"][0]
        assert r["sections_min"] == 3

    def test_chanson_complete_de_plus_de_120s_passe(self, moteur_v1):
        # Le cas visé : > 120 s découpés, texte complet, rien de tronqué.
        r = ss.controler_couverture(_paroles(6, 60), 180)
        assert r["couvrable"] is True
        assert r["segments"] == 2 and r["sections"] == 6
        assert r["problemes"] == []

    def test_texte_clairseme_avertit_sans_refuser(self):
        # Le daemon avertit déjà sous 1 mot/s — mais dans un print de sous-processus
        # que personne ne lit. On le remonte à l'appelant, sans bloquer.
        r = ss.controler_couverture(_paroles(3, 10), 300)
        assert r["couvrable"] is True
        assert any("clairsemé" in a for a in r["avertissements"])

    def test_duree_deduite_quand_absente(self):
        r = ss.controler_couverture(_paroles(4, 50), None)
        assert r["duree_demandee_sec"] is None
        assert r["duree_sec"] == r["duree_conseillee_sec"] == 100  # 200 mots / 2
        assert r["couvrable"] is True

    def test_duree_deduite_ne_se_refuse_jamais_elle_meme(self):
        # La durée déduite tombe sur la cible : elle ne peut pas violer le débit.
        # Reste le critère sections/segments, qui lui peut mordre — d'où le test.
        r = ss.controler_couverture(_paroles(8, 100), None)
        assert r["debit_mots_s"] == pytest.approx(ss.DEBIT_CIBLE, abs=0.05)
        assert r["couvrable"] is True

    def test_bornes_du_daemon_appliquees(self):
        assert ss.controler_couverture(_paroles(2, 5), 5)["duree_sec"] == ss.DUREE_MIN_SEC
        assert ss.controler_couverture(_paroles(9, 200), 9999)["duree_sec"] == ss.DUREE_MAX_SEC

    def test_aucune_parole(self):
        r = ss.controler_couverture("", None)
        assert r["couvrable"] is False and r["mots"] == 0

    def test_le_refus_nomme_lechappatoire(self):
        r = ss.controler_couverture(_paroles(4, 100), 30)
        assert "forcer=True" in ss.message_de_refus(r)


# --------------------------------------------------------------------------- #
# Anti-dérive : les constantes recopiées valent-elles encore ce qu'elles disent ? #
# --------------------------------------------------------------------------- #


_PAROLES_TEMOIN = (
    "[Couplet 1]\nje marche seul la nuit la ville dort enfin\n\n"
    "Refrain :\net je crie ton nom encore et encore\n\n"
    "[Couplet 2]\nle matin se leve je ne dors toujours pas\n\n"
    "[Pont]\nrien ne me retient plus ici\n\n"
    "[Refrain]\net je crie ton nom encore et encore"
)

# Variables d'environnement qui décident du plafond de segment, des DEUX côtés.
# Retirées puis reposées par régime : un shell de dev qui exporte l'une d'elles ne
# doit pas décider de ce que le test compare.
_VARIABLES_DU_PLAFOND = ("ACE_STEP_VERSION", "ACESTEP_MAX_SEGMENT_SEC", "ACESTEP_SEGMENT_OVERLAP_SEC")

# Les deux régimes que le daemon sait servir aujourd'hui.
#   - `defaut` : ce qu'il fait sans consigne — la production, une passe en v1.5 ;
#   - `v1`     : le rollback moteur, SEUL régime où le découpage tourne encore,
#                donc seul où la répartition entre segments peut être confrontée.
# ⚠️ Juger le seul régime par défaut aurait rendu ce garde décoratif : à 600 s de
# plafond, `nb_segments` vaut 1 partout et n'importe quelle boucle de découpage,
# même fausse, passe. Mesuré le 2026-09-27 par mutations : 3 sur 6 au vert en
# `defaut` seul (600 en dur, boucle de chevauchement retirée, équilibrage des
# groupes inversé — celle-ci n'est attrapée par AUCUN autre test) ; 6 sur 6 avec v1.
_REGIMES: dict[str, dict[str, str]] = {"defaut": {}, "v1": {"ACE_STEP_VERSION": "v1"}}


def _env(regime: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _VARIABLES_DU_PLAFOND}
    env.update(regime)
    return env


def _constantes_klody(regime: dict[str, str]) -> dict:
    """Constantes de `song_structure` telles qu'un process frais les lit sous `regime`.

    Sous-processus plutôt que `importlib.reload` : le reload changerait le module
    partagé par toute la suite, et c'est l'expression de NIVEAU MODULE — condition
    et surcharge comprises — qui doit être jugée, pas une fonction extraite.
    """
    code = (
        "import json; from klody_mcp import song_structure as ss; print(json.dumps("
        "{'version': ss.ACE_STEP_VERSION, 'segment_max': ss.SEGMENT_MAX_SEC,"
        " 'overlap': ss.SEGMENT_OVERLAP_SEC}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], env=_env(regime), cwd=str(_RACINE),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# Sonde exécutée DANS local-suno, avec son interpréteur et son cwd. Elle rejoue le
# circuit réel : parseur de paroles custom → reconstruction de l'arrangement →
# répartition entre segments. C'est ce que `generate_song_long` chanterait.
#
# ⚠️ Pourquoi un sous-processus et pas un `sys.path.append` : les DEUX dépôts ont
# un module `config` (et un `main`). Importé en cours de suite, `config` est déjà
# celui de klody-code-ai dans `sys.modules`, et `pipeline.acestep_generator` échoue
# sur `ACE_STEP_SEED`. Écrit en import direct, ce garde-fou se SAUTAIT donc en
# silence sur la machine même où il devait mordre — un test sauté est
# indiscernable d'un test vert.
#
# Le cwd est local-suno : son `config` fait `load_dotenv()`, donc un `.env` posé
# là (rollback v1, plafond surchargé) entre dans la mesure, comme pour le daemon.
# Ce que la sonde ne voit PAS : l'environnement du plist launchd du daemon.
_SONDE = r"""
import json, sys
sys.path.insert(0, ".")
from config import ACE_STEP_VERSION, ACESTEP_MAX_SEGMENT_SEC, ACESTEP_SEGMENT_OVERLAP_SEC
from main import _build_lyrics_from_custom
from pipeline.acestep_generator import plan_segment_durations
from pipeline.song_format import build_arrangement, split_arrangement_text

entree = json.loads(sys.stdin.read())
arrangement = entree["arrangement"]

# Répartition réelle d'un arrangement à sections INÉGALES : c'est elle qui décide
# du débit de chaque segment, et donc de ce qui sera tronqué.
inegal = entree["inegal"]
n_inegal = len(plan_segment_durations(180))
mots_par_segment = [
    sum(len(l.split()) for l in c.splitlines() if not l.strip().startswith("["))
    for c in split_arrangement_text(inegal, n_inegal)
]

ly = _build_lyrics_from_custom(arrangement, "t", "pop", 90, "Am")
reconstruit = build_arrangement(ly.structure, ly.lyrics)
n = len(plan_segment_durations(180))
morceaux = split_arrangement_text(reconstruit, n)
while len(morceaux) < n:
    morceaux.append(morceaux[-1])

print(json.dumps({
    "version": ACE_STEP_VERSION,
    "segment_max": ACESTEP_MAX_SEGMENT_SEC,
    "overlap": ACESTEP_SEGMENT_OVERLAP_SEC,
    "segments_par_duree": {str(d): len(plan_segment_durations(d))
                           for d in (30, 120, 121, 180, 240, 300, 360, 600)},
    "sections_gardees": list(ly.lyrics),
    "reconstruit": reconstruit,
    "morceaux": morceaux,
    "mots_par_segment_inegal": mots_par_segment,
}))
"""


@pytest.fixture(scope="module")
def daemon_reel() -> dict:
    """Faits relevés dans le VRAI local-suno, par régime, ou skip explicite s'il est absent.

    Chaque régime porte aussi, sous la clé ``klody``, les constantes que
    `song_structure` lit dans CE MÊME environnement : les deux côtés sont mesurés
    sous les mêmes variables, jamais l'un contre la mémoire de l'autre.
    """
    if not _LOCALSUNO.is_dir():
        pytest.skip("local-suno absent de cette machine — rien à confronter")
    python = _LOCALSUNO / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"venv local-suno absent ({python})")
    arrangement, _ = ss.canonicaliser_paroles(_PAROLES_TEMOIN)
    inegal, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
    faits: dict[str, dict] = {}
    for nom, regime in _REGIMES.items():
        proc = subprocess.run(
            [str(python), "-c", _SONDE],
            input=json.dumps({"arrangement": arrangement, "inegal": inegal}),
            capture_output=True, text=True, cwd=str(_LOCALSUNO), env=_env(regime),
            timeout=120,
        )
        if proc.returncode != 0:
            pytest.fail(
                f"la sonde local-suno a échoué (régime {nom}) — le contrat a peut-être "
                "changé :\n" + proc.stderr[-1500:]
            )
        faits[nom] = json.loads(proc.stdout.strip().splitlines()[-1])
        faits[nom]["klody"] = _constantes_klody(regime)
    return faits


@pytest.fixture(params=sorted(_REGIMES))
def regime(request, daemon_reel, monkeypatch) -> dict:
    """Un régime du daemon, avec `ss` réglé sur ce que Klody lit dans le même env.

    Les valeurs posées viennent du sous-processus Klody, pas du daemon : sinon
    `nb_segments` serait calculé avec le plafond du daemon et le test comparerait
    le daemon à lui-même.
    """
    faits = daemon_reel[request.param]
    monkeypatch.setattr(ss, "SEGMENT_MAX_SEC", faits["klody"]["segment_max"])
    monkeypatch.setattr(ss, "SEGMENT_OVERLAP_SEC", faits["klody"]["overlap"])
    return faits


@pytest.mark.slow
class TestPasDeDerive:
    """Confronte les constantes et le circuit au VRAI dépôt local-suno.

    Motif : `_idee_to_body` bornait la durée à 120 s en citant « bornes daemon
    (ge=10 le=120) » alors que le contrat était passé à 600 depuis longtemps.
    Toute démo au-delà de 2 min était donc silencieusement coupée de moitié, et
    rien ne pouvait rougir. Une constante recopiée sans test qui la relit est une
    constante qui ment tôt ou tard.

    Rougi pour de vrai le 2026-09-27 : plafond de segment passé à 600 s en v1.5
    côté daemon (local-suno 3fddc2c, 2026-09-09), resté à 120 s ici.
    """

    def test_les_bornes_de_duree_et_de_bpm_nont_pas_bouge(self):
        if not _LOCALSUNO.is_dir():
            pytest.skip("local-suno absent de cette machine")
        source = (_LOCALSUNO / "storage" / "models.py").read_text(encoding="utf-8")
        assert f"ge={ss.DUREE_MIN_SEC}, le={ss.DUREE_MAX_SEC}" in source, (
            "duration_sec a changé de bornes côté daemon — mets à jour song_structure"
        )
        assert f"ge={ss.BPM_MIN}, le={ss.BPM_MAX}" in source, (
            "bpm a changé de bornes côté daemon — mets à jour song_structure"
        )

    def test_la_version_moteur_est_celle_du_daemon(self, regime):
        # Le plafond dépend du moteur : lire la même version des deux côtés est la
        # prémisse de tout le reste. En `defaut`, c'est le défaut du daemon qui est
        # jugé — une bascule de moteur par défaut rougirait ici en premier.
        assert regime["klody"]["version"] == regime["version"]

    def test_le_plafond_de_segment_na_pas_bouge(self, regime):
        assert regime["klody"]["segment_max"] == regime["segment_max"]
        assert regime["klody"]["overlap"] == regime["overlap"]

    def test_le_regime_v1_decoupe_encore(self, daemon_reel):
        # Sentinelle du garde lui-même : si v1 cessait de découper (plafond relevé,
        # régime retiré), toutes les confrontations de répartition deviendraient
        # triviales — un segment partout — et passeraient sans rien juger.
        assert daemon_reel["v1"]["segments_par_duree"]["180"] == 2

    def test_le_compte_de_segments_est_celui_du_daemon(self, regime):
        calcule = {d: ss.nb_segments(int(d)) for d in regime["segments_par_duree"]}
        assert calcule == regime["segments_par_duree"]

    def test_le_daemon_garde_TOUTES_les_sections(self, daemon_reel):
        # Sans la numérotation des marqueurs, le 2ᵉ [Refrain] écrase le 1ᵉʳ dans le
        # dict de sections du daemon : un refrain disparaît de la chanson.
        assert len(daemon_reel["defaut"]["sections_gardees"]) == 5, (
            f"sections écrasées : {daemon_reel['defaut']['sections_gardees']}"
        )

    def test_la_repartition_des_mots_est_celle_du_daemon(self, regime):
        """`debit_par_segment` doit prédire le VRAI découpage, pas une approximation.

        C'est ce chiffre qui décide de la durée déduite : s'il diverge de ce que
        `split_arrangement_text` fait réellement, la durée choisie ne désature rien
        et le garde-fou devient décoratif.
        """
        arrangement, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
        predit = [m for m, _ in ss.debit_par_segment(arrangement, 180)]
        assert predit == regime["mots_par_segment_inegal"]

    def test_le_temoin_reste_desequilibre_quand_on_decoupe(self, daemon_reel):
        # Sans déséquilibre réel côté daemon, la confrontation précédente ne
        # prouverait pas que l'équilibrage se fait en NOMBRE de sections.
        premier, second = daemon_reel["v1"]["mots_par_segment_inegal"]
        assert premier > second

    def test_circuit_complet_chaque_segment_chante_autre_chose(self, regime):
        """Le test qui porte la conclusion : jamais de texte re-chanté.

        En v1 : 180 s = 2 segments, chacun sa part du texte. En une passe : un seul
        morceau, qui est le texte ENTIER.
        """
        morceaux = regime["morceaux"]
        assert len(morceaux) == ss.nb_segments(180)
        assert len(set(morceaux)) == len(morceaux), (
            "des segments chanteraient le même texte"
        )
        if len(morceaux) == 1:
            assert morceaux == [regime["reconstruit"]]

    def test_aucune_parole_perdue_en_route(self, daemon_reel):
        reconstruit = daemon_reel["defaut"]["reconstruit"]
        for ligne in _PAROLES_TEMOIN.splitlines():
            t = ligne.strip()
            if t and not t.startswith("[") and not t.lower().startswith("refrain"):
                assert t in reconstruit, f"parole perdue : {t!r}"
