"""Tests de klody_mcp.song_structure — le contrôle de couverture des paroles.

Ce module décide si une génération de chanson pourra chanter TOUT le texte. Ses
deux verdicts négatifs correspondent à deux mécanismes mesurés dans le daemon
local-suno, pas à des heuristiques de prudence :

- trop de mots pour la durée ⇒ le moteur coupe ;
- moins de sections que de segments ⇒ ``generate_song_long`` recopie la dernière
  section dans les segments restants (ils re-chantent la même chose). En mode
  DÉCOUPÉ seulement : depuis local-suno ``3fddc2c`` (2026-09-09), ACE-Step 1.5
  compose en une passe jusqu'à 600 s.

Chaque test qui dépend du plafond de segment le POSE (``plafond_une_passe`` par
défaut, ``plafond_decoupe`` explicitement) : lu dans l'environnement, il ferait
juger le ``.env`` du développeur.

La dernière classe (`TestPasDeDerive`) relit la vraie règle de ``~/local-suno`` et
rejoue le circuit complet à travers SON parseur et SON ``generate_song_long``, dans
les deux modes. Elle se saute si le dépôt est absent de la machine — elle ne peut
alors pas juger, et le dit.
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

pytestmark = pytest.mark.usefixtures("plafond_une_passe")


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
    """La règle du daemon, verrouillée sur des LITTÉRAUX.

    Un attendu recalculé depuis `plafond_segment` suivrait la règle au lieu de la
    juger — la mutation `MUETTE_JOURS → 99999` a échappé exactement comme ça.
    """

    @pytest.mark.parametrize(
        ("version", "surcharge", "attendu"),
        [("v15", None, 600.0), ("v1", None, 120.0), ("v15", "120", 120.0), ("v1", "600", 600.0)],
    )
    def test_regle(self, version, surcharge, attendu):
        assert ss.plafond_segment(version, surcharge) == attendu

    @pytest.mark.parametrize(
        ("env", "attendu"),
        [
            ({}, 600.0),
            ({"ACE_STEP_VERSION": "v1"}, 120.0),
            ({"ACESTEP_MAX_SEGMENT_SEC": "120"}, 120.0),
        ],
    )
    def test_la_constante_du_module_suit_lenvironnement(self, env, attendu):
        # Sous-processus : recharger le module en cours de suite recréerait ses
        # objets (piège `importlib.reload`), et le monkeypatch des fixtures masque
        # justement la valeur calculée à l'import — celle qu'on veut juger ici.
        base = {
            k: v for k, v in os.environ.items()
            if k not in ("ACE_STEP_VERSION", "ACESTEP_MAX_SEGMENT_SEC")
        }
        proc = subprocess.run(
            [sys.executable, "-c",
             "from klody_mcp import song_structure as ss; print(ss.SEGMENT_MAX_SEC)"],
            env={**base, **env}, cwd=str(_RACINE),
            capture_output=True, text=True, check=True, timeout=60,
        )
        assert float(proc.stdout.strip()) == attendu


@pytest.mark.usefixtures("plafond_decoupe")
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


@pytest.mark.usefixtures("plafond_decoupe")
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


class TestUnePasse:
    """Défaut du daemon depuis local-suno 3fddc2c : ACE-Step 1.5 compose d'un bloc.

    Le plafond (600 s) égale la borne haute des durées acceptées : aucune chanson
    n'est découpée, donc ni répartition des sections ni segment à désaturer.
    """

    @pytest.mark.parametrize("duree", [30, 120, 121, 180, 240, 360, 600])
    def test_un_seul_segment_sur_toute_la_plage(self, duree):
        assert ss.nb_segments(duree) == 1

    def test_un_texte_dun_seul_bloc_nest_plus_refuse(self):
        # Le faux refus vécu du 2026-09-09 au 2026-09-27 : 240 s sur un bloc
        # unique « RE-CHANTERAIT » en mode découpé. En une passe, le moteur reçoit
        # tout le texte d'un coup — il n'y a rien à répéter.
        r = ss.controler_couverture(" ".join(["mot"] * 480), 240)
        assert r["couvrable"] is True, r["problemes"]
        assert r["segments"] == 1
        # L'hypothèse sur la config du daemon se LIT dans le rapport.
        assert r["plafond_segment_sec"] == 600

    def test_le_debit_du_segment_est_le_debit_global(self):
        # Ces sections inégales saturaient le 1ᵉʳ segment en mode découpé
        # (TestDebitParSegment). D'un bloc, 338 mots sur 180 s = 1,9 mots/s.
        r = ss.controler_couverture(_SECTIONS_INEGALES, 180)
        assert r["mots_par_segment"] == [r["mots"]]
        assert r["debit_pire_segment"] == r["debit_mots_s"]
        assert r["avertissements"] == []

    def test_la_duree_deduite_ne_paie_plus_le_desequilibre(self):
        # En mode découpé, ce texte impose une durée au-delà du plancher global
        # (test_la_duree_deduite_desature_le_pire_segment). En une passe, non :
        # l'allonger étirerait le chant pour désaturer un segment qui n'existe pas.
        r = ss.controler_couverture(_SECTIONS_INEGALES, None)
        assert r["duree_sec"] == ss.duree_conseillee(r["mots"])

    def test_la_densite_reste_refusee(self):
        # Le mécanisme n°1 ne dépend pas du découpage : trop de mots, le moteur coupe.
        r = ss.controler_couverture(_paroles(4, 100), 30)
        assert r["couvrable"] is False
        assert "coupera des sections" in r["problemes"][0]


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

    def test_moins_de_sections_que_de_segments_est_refuse(self, plafond_decoupe):
        # 240 s = 3 segments ; 2 sections ⇒ le 3ᵉ segment re-chante le 2ᵉ.
        r = ss.controler_couverture(_paroles(2, 240), 240)
        assert r["couvrable"] is False
        assert "RE-CHANTERONT" in r["problemes"][0]
        # Le refus nomme l'hypothèse qui le fonde : elle vit dans un autre processus.
        assert "plafond de segment 120 s" in r["problemes"][0]
        assert r["sections_min"] == 3

    def test_chanson_complete_de_plus_de_120s_passe(self, plafond_decoupe):
        # Le cas visé : > 120 s, texte complet, rien de tronqué.
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

# Sonde exécutée DANS local-suno, avec son interpréteur et son cwd. Elle rejoue le
# circuit réel de `custom_lyrics` : parseur de paroles custom → reconstruction de
# l'arrangement → `generate_song_long`, le VRAI, moteur remplacé par un
# enregistreur. `appels` est donc exactement le texte que chaque appel ACE-Step
# recevrait — une passe ou N segments, selon la config du daemon.
#
# ⚠️ La sonde recopiait auparavant la boucle `chunks.append(chunks[-1])` et
# appelait `split_arrangement_text` elle-même : une copie de plus, qui aurait pu
# dériver comme les constantes. Passer par la vraie fonction la fait disparaître.
#
# ⚠️ Pourquoi un sous-processus et pas un `sys.path.append` : les DEUX dépôts ont
# un module `config` (et un `main`). Importé en cours de suite, `config` est déjà
# celui de klody-code-ai dans `sys.modules`, et `pipeline.acestep_generator` échoue
# sur `ACE_STEP_SEED`. Écrit en import direct, ce garde-fou se SAUTAIT donc en
# silence sur la machine même où il devait mordre — un test sauté est
# indiscernable d'un test vert.
_SONDE = r"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
import numpy as np
import soundfile as sf
from config import ACESTEP_MAX_SEGMENT_SEC, ACESTEP_SEGMENT_OVERLAP_SEC
from main import _build_lyrics_from_custom
from pipeline.acestep_generator import generate_song_long, plan_segment_durations
from pipeline.song_format import build_arrangement

def chante(arrangement, duree):
    appels = []
    def moteur(prompt, texte, sortie, dur, graine):
        appels.append(texte)
        sf.write(str(sortie), np.zeros(int(dur * 100) + 1, dtype=np.float32), 100)
        return sortie
    with tempfile.TemporaryDirectory() as d:
        generate_song_long("t", arrangement, Path(d) / "chanson.wav", duree,
                           segment_generator=moteur)
    return appels

entree = json.loads(sys.stdin.read())
cas = {}
for nom, (paroles, duree) in entree.items():
    ly = _build_lyrics_from_custom(paroles, "t", "pop", 90, "Am")
    reconstruit = build_arrangement(ly.structure, ly.lyrics)
    cas[nom] = {"sections_gardees": list(ly.lyrics), "reconstruit": reconstruit,
                "appels": chante(reconstruit, duree)}

print(json.dumps({
    "segment_max": ACESTEP_MAX_SEGMENT_SEC,
    "overlap": ACESTEP_SEGMENT_OVERLAP_SEC,
    "segments_par_duree": {str(d): len(plan_segment_durations(d))
                           for d in (30, 120, 121, 180, 240, 300, 360, 600)},
    "cas": cas,
}))
"""

_SONDE_REGLE = (
    "import json, sys; sys.path.insert(0, '.'); "
    "from config import ACESTEP_MAX_SEGMENT_SEC as m, ACESTEP_SEGMENT_OVERLAP_SEC as o; "
    "print(json.dumps([m, o]))"
)

# Un texte d'un seul bloc : le cas que le mode découpé re-chante, et que la passe
# unique chante entier. C'est lui qui prouve que le verdict de Klody suit le daemon.
_UN_BLOC = " ".join(["mot"] * 480)

# Cas rejoués dans le daemon : (paroles BRUTES, durée). La sonde reçoit
# l'arrangement canonique, c'est-à-dire ce que Klody envoie vraiment.
_CAS = {
    "temoin": (_PAROLES_TEMOIN, 180),
    "inegal": (_SECTIONS_INEGALES, 180),
    "un_bloc": (_UN_BLOC, 240),
}

# Les deux modes du daemon, tels qu'on les obtient de SON environnement, et la
# config équivalente côté Klody. « une_passe » tourne SANS les deux variables :
# un `~/local-suno/.env` qui les poserait fait donc rougir ces tests — voulu, le
# daemon réel serait alors dans un mode que Klody ignore.
_MODES = {
    "une_passe": ({}, ("v15", None)),
    "decoupe": ({"ACE_STEP_VERSION": "v1"}, ("v1", None)),
}

# (ACE_STEP_VERSION, ACESTEP_MAX_SEGMENT_SEC) — None = variable absente.
_VARIANTES_REGLE = [
    (None, None), ("v15", None), ("v1", None),
    ("v15", "120"), ("v1", "600"), ("v15", "300"),
]


def _env_daemon(**poser: str) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("ACE_STEP_VERSION", "ACESTEP_MAX_SEGMENT_SEC")
    }
    env.update(poser)
    return env


def _sonder(python: Path, code: str, env: dict, entree: str = "") -> dict | list:
    proc = subprocess.run(
        [str(python), "-c", code], input=entree, env=env,
        capture_output=True, text=True, cwd=str(_LOCALSUNO), timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            "la sonde local-suno a échoué — le contrat a peut-être changé :\n"
            + proc.stderr[-1500:]
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def daemon_reel() -> dict:
    """Faits relevés dans le VRAI local-suno, ou skip explicite s'il est absent."""
    if not _LOCALSUNO.is_dir():
        pytest.skip("local-suno absent de cette machine — rien à confronter")
    python = _LOCALSUNO / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"venv local-suno absent ({python})")

    regle = []
    for version, surcharge in _VARIANTES_REGLE:
        poser = {}
        if version is not None:
            poser["ACE_STEP_VERSION"] = version
        if surcharge is not None:
            poser["ACESTEP_MAX_SEGMENT_SEC"] = surcharge
        plafond, overlap = _sonder(python, _SONDE_REGLE, _env_daemon(**poser))
        regle.append((version, surcharge, plafond, overlap))

    entree = json.dumps({
        nom: [ss.canonicaliser_paroles(paroles)[0], duree]
        for nom, (paroles, duree) in _CAS.items()
    })
    modes = {
        mode: _sonder(python, _SONDE, _env_daemon(**env), entree)
        for mode, (env, _) in _MODES.items()
    }
    return {"regle": regle, "modes": modes}


@pytest.fixture(params=list(_MODES))
def mode(request, plafond_une_passe, monkeypatch) -> str:
    """Configure Klody comme le daemon de ce mode — par SA règle, pas un littéral.

    C'est l'affirmation de bout en bout : « Klody, réglé comme le daemon, prédit ce
    que le daemon fait ». Une règle fausse côté Klody rougit donc ici aussi.
    """
    _, (version, surcharge) = _MODES[request.param]
    monkeypatch.setattr(ss, "SEGMENT_MAX_SEC", ss.plafond_segment(version, surcharge))
    return request.param


@pytest.mark.slow
class TestPasDeDerive:
    """Confronte les constantes et le circuit au VRAI dépôt local-suno.

    Motif : `_idee_to_body` bornait la durée à 120 s en citant « bornes daemon
    (ge=10 le=120) » alors que le contrat était passé à 600 depuis longtemps.
    Toute démo au-delà de 2 min était donc silencieusement coupée de moitié, et
    rien ne pouvait rougir. Une constante recopiée sans test qui la relit est une
    constante qui ment tôt ou tard.

    Deuxième morsure, le 2026-09-27 : le plafond de segment est devenu une RÈGLE
    (600 s en v1.5, 120 s en v1) dans local-suno 3fddc2c. Ce garde a rougi — il a
    fait son travail — mais il ne comparait qu'une valeur sous un seul
    environnement. Il compare maintenant la règle entière et le circuit dans les
    deux modes.
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

    def test_la_regle_du_plafond_est_celle_du_daemon(self, daemon_reel):
        for version, surcharge, plafond, overlap in daemon_reel["regle"]:
            attendu = (
                ss.plafond_segment(surcharge=surcharge) if version is None
                else ss.plafond_segment(version, surcharge)
            )
            assert attendu == plafond, (
                f"ACE_STEP_VERSION={version} ACESTEP_MAX_SEGMENT_SEC={surcharge} : "
                f"le daemon découpe à {plafond} s, Klody croit {attendu} s"
            )
            assert overlap == ss.SEGMENT_OVERLAP_SEC

    def test_le_compte_de_segments_est_celui_du_daemon(self, daemon_reel, mode):
        reel = daemon_reel["modes"][mode]
        assert reel["segment_max"] == ss.SEGMENT_MAX_SEC
        calcule = {d: ss.nb_segments(int(d)) for d in reel["segments_par_duree"]}
        assert calcule == reel["segments_par_duree"]

    def test_le_daemon_garde_TOUTES_les_sections(self, daemon_reel, mode):
        # Sans la numérotation des marqueurs, le 2ᵉ [Refrain] écrase le 1ᵉʳ dans le
        # dict de sections du daemon : un refrain disparaît de la chanson.
        gardees = daemon_reel["modes"][mode]["cas"]["temoin"]["sections_gardees"]
        assert len(gardees) == 5, f"sections écrasées : {gardees}"

    def test_la_repartition_des_mots_est_celle_du_daemon(self, daemon_reel, mode):
        """`debit_par_segment` doit prédire le VRAI découpage, pas une approximation.

        C'est ce chiffre qui décide de la durée déduite : s'il diverge de ce que
        `generate_song_long` envoie réellement, la durée choisie ne désature rien
        (ou allonge le chant pour rien) et le garde-fou devient décoratif.
        """
        arrangement, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
        predit = [m for m, _ in ss.debit_par_segment(arrangement, 180)]
        appels = daemon_reel["modes"][mode]["cas"]["inegal"]["appels"]
        assert predit == [ss.mots_chantes(t) for t in appels]
        if mode == "decoupe":
            assert predit[0] > predit[1], "le témoin doit rester déséquilibré"
        else:
            assert len(predit) == 1, "une passe : tout le texte dans un seul appel"

    def test_le_verdict_de_repetition_est_celui_du_daemon(self, daemon_reel, mode):
        """Le test qui porte la conclusion : Klody refuse SSI le daemon re-chante.

        Un refus que le daemon ne justifie pas est aussi faux qu'un refus manquant
        — c'est ce que Klody rendait du 2026-09-09 au 2026-09-27, en une passe.
        """
        cas = daemon_reel["modes"][mode]["cas"]
        repete = {nom: len(set(c["appels"])) < len(c["appels"]) for nom, c in cas.items()}
        # Le témoin doit garder son pouvoir de discrimination dans les deux sens.
        assert repete["un_bloc"] is (mode == "decoupe")
        assert repete["temoin"] is False and repete["inegal"] is False

        for nom, (paroles, duree) in _CAS.items():
            r = ss.controler_couverture(paroles, duree)
            refuse = any("RE-CHANTERONT" in p for p in r["problemes"])
            assert refuse is repete[nom], (
                f"{nom} ({duree} s, mode {mode}) : le daemon "
                f"{'re-chante' if repete[nom] else 'chante tout'}, "
                f"Klody {'refuse' if refuse else 'accepte'}"
            )
            assert r["segments"] == len(cas[nom]["appels"])

    def test_circuit_complet_chaque_segment_chante_autre_chose(self, daemon_reel, mode):
        """> 120 s sans texte re-chanté, dans les deux modes."""
        c = daemon_reel["modes"][mode]["cas"]["temoin"]
        attendu = 2 if mode == "decoupe" else 1
        assert len(c["appels"]) == attendu, f"180 s = {attendu} appel(s) en {mode}"
        assert len(set(c["appels"])) == len(c["appels"]), (
            "des segments chanteraient le même texte"
        )
        if mode == "une_passe":
            assert c["appels"] == [c["reconstruit"]]

    def test_aucune_parole_perdue_en_route(self, daemon_reel, mode):
        chante = "\n".join(daemon_reel["modes"][mode]["cas"]["temoin"]["appels"])
        for ligne in _PAROLES_TEMOIN.splitlines():
            t = ligne.strip()
            if t and not t.startswith("[") and not t.lower().startswith("refrain"):
                assert t in chante, f"parole perdue : {t!r}"
