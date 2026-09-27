"""Tests de klody_mcp.song_structure — le contrôle de couverture des paroles.

Ce module décide si une génération de chanson pourra chanter TOUT le texte. Ses
deux verdicts négatifs correspondent à deux mécanismes mesurés dans le daemon
local-suno, pas à des heuristiques de prudence :

- trop de mots pour la durée ⇒ le moteur coupe ;
- moins de sections que de segments ⇒ ``generate_song_long`` recopie la dernière
  section dans les segments restants (ils re-chantent la même chose).

La dernière classe (`TestPasDeDerive`) relit les vraies constantes de
``~/local-suno`` et rejoue le circuit complet à travers SON parseur. Elle se saute
si le dépôt est absent de la machine — elle ne peut alors pas juger, et le dit.

Deux modes, depuis local-suno ``3fddc2c`` (2026-09-09) :

- **une passe** (nominal, v1.5) : plafond de segment 600 s = durée maximale du
  contrat, donc jamais plus d'un segment ;
- **découpé** (v1, ou ``ACESTEP_MAX_SEGMENT_SEC=120`` posé côté daemon) : le
  chemin historique, où le second mécanisme mord. Toujours vivant chez le daemon,
  donc toujours testé ici — mais sous une fixture explicite (`chanson_decoupee`),
  jamais en comptant sur l'environnement de la machine qui lance la suite.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from klody_mcp import song_structure as ss

_LOCALSUNO = Path.home() / "local-suno"

# Plafond du mode découpé passé à la sonde, en LITTÉRAL : un test qui se
# recalcule à partir du réglage qu'il protège ne peut pas rougir (mutation
# échappée de la veille Qwen, CLAUDE.md 2026-08-10). Le mode lui-même se règle
# par les fixtures de conftest (`_chanson_en_une_passe`, `chanson_decoupee`).
_PLAFOND_DECOUPE = 120.0


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


class TestPlafond:
    """Le défaut du plafond suit la version du moteur, comme `config.py` du daemon.

    Même matrice que local-suno/tests/test_acestep_long.py, en littéraux.
    """

    @pytest.mark.parametrize(
        ("env", "attendu"),
        [
            ({}, 600.0),  # défaut du daemon : v1.5
            ({"ACE_STEP_VERSION": "v15"}, 600.0),
            ({"ACE_STEP_VERSION": "v1"}, 120.0),  # v1 garde son plafond historique
            ({"ACE_STEP_VERSION": "v15", "ACESTEP_MAX_SEGMENT_SEC": "120"}, 120.0),
        ],
    )
    def test_defaut_selon_la_version_et_surcharge_prioritaire(self, env, attendu):
        assert ss.plafond_segment(env) == attendu

    def test_le_plafond_nominal_couvre_toute_la_plage_du_contrat(self):
        # C'est ce qui rend le second mécanisme inopérant en nominal : aucune
        # durée acceptée par le daemon ne dépasse le plafond.
        assert ss.plafond_segment({}) >= ss.DUREE_MAX_SEC


class TestSegments:
    @pytest.mark.usefixtures("chanson_decoupee")
    @pytest.mark.parametrize(
        ("duree", "attendu"), [(30, 1), (120, 1), (121, 2), (180, 2), (240, 3), (360, 4)]
    )
    def test_nombre_de_segments_en_mode_decoupe(self, duree, attendu):
        assert ss.nb_segments(duree) == attendu

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_le_chevauchement_peut_ajouter_un_segment(self):
        # 240 s : ceil(240/120) = 2 donnerait des segments de 122 s > plafond.
        # Reproduire la seule division ferait sous-estimer d'un segment.
        assert ss.nb_segments(240) == 3

    def test_en_une_passe_jamais_plus_dun_segment(self):
        assert {ss.nb_segments(d) for d in range(ss.DUREE_MIN_SEC, ss.DUREE_MAX_SEC + 1)} == {1}


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


@pytest.mark.usefixtures("chanson_decoupee")
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
    """Le mode nominal depuis 2026-09-09 : aucun segment fantôme.

    Chaque test est le miroir d'un verdict du mode découpé qui, appliqué à un
    daemon en une passe, était FAUX — c'est ce que ce module rendait tant qu'il
    recopiait l'ancien plafond de 120 s.
    """

    def test_le_temoin_inegal_ne_sature_plus_rien(self):
        # En découpé : 2,4 mots/s sur le segment 1 et un avertissement. En une
        # passe il n'y a qu'un débit, le global, et il est conforme.
        r = ss.controler_couverture(_SECTIONS_INEGALES, 180)
        assert r["segments"] == 1
        assert r["debit_pire_segment"] == r["debit_mots_s"] <= ss.DEBIT_CIBLE
        assert r["avertissements"] == []

    def test_la_duree_deduite_nest_plus_gonflee(self):
        # En découpé : 218 s, pour désaturer un segment que le daemon ne fait plus
        # — soit un chant étiré à ~1,55 mot/s. En une passe : le plancher global.
        r = ss.controler_couverture(_SECTIONS_INEGALES, None)
        assert r["duree_sec"] == ss.duree_conseillee(r["mots"]) == 169

    def test_peu_de_sections_nest_plus_refuse(self):
        # En découpé : 240 s = 3 segments pour 2 sections ⇒ refus RE-CHANTERONT.
        # En une passe, tout le texte part dans l'unique appel : rien à re-chanter.
        r = ss.controler_couverture(_paroles(2, 240), 240)
        assert r["couvrable"] is True
        assert r["problemes"] == []
        # L'hypothèse sur le mode du daemon se LIT dans le rapport.
        assert r["plafond_segment_sec"] == 600


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

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_moins_de_sections_que_de_segments_est_refuse(self):
        # 240 s = 3 segments ; 2 sections ⇒ le 3ᵉ segment re-chante le 2ᵉ.
        r = ss.controler_couverture(_paroles(2, 240), 240)
        assert r["couvrable"] is False
        assert "RE-CHANTERONT" in r["problemes"][0]
        # Le refus nomme l'hypothèse qui le fonde : elle vit dans un autre processus.
        assert "plafond de segment 120 s" in r["problemes"][0]
        assert r["plafond_segment_sec"] == 120
        assert r["sections_min"] == 3

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_chanson_complete_de_plus_de_120s_passe(self):
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
# circuit réel : parseur de paroles custom → reconstruction de l'arrangement →
# `generate_song_long`, le VRAI, moteur remplacé par un enregistreur. Chaque liste
# de morceaux est donc exactement le texte que chaque appel ACE-Step recevrait.
#
# ⚠️ La sonde recopiait la boucle `chunks.append(chunks[-1])` et appelait
# `split_arrangement_text` elle-même : une copie de plus dans un garde contre les
# copies, capable de dériver en silence comme les constantes. Passer par la vraie
# fonction la fait disparaître.
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

entree = json.loads(sys.stdin.read())
arrangement = entree["arrangement"]
# Le plafond du mode découpé est passé EXPLICITEMENT : le défaut du daemon est
# désormais 600 s, et ce chemin n'est plus exercé qu'en surcharge (ou en v1).
decoupe = entree["plafond_decoupe"]

def plan(d, plafond=None):
    if plafond is None:
        return plan_segment_durations(d)  # le défaut effectif du daemon
    return plan_segment_durations(d, plafond, ACESTEP_SEGMENT_OVERLAP_SEC)

def chante(texte, duree, plafond=None):
    # Le texte que generate_song_long envoie à CHAQUE appel du moteur.
    appels = []
    def moteur(prompt, paroles, sortie, dur, graine):
        appels.append(paroles)
        sf.write(str(sortie), np.zeros(int(dur * 100) + 1, dtype=np.float32), 100)
        return sortie
    options = {} if plafond is None else {"max_segment_sec": plafond}
    with tempfile.TemporaryDirectory() as d:
        generate_song_long("t", texte, Path(d) / "chanson.wav", duree,
                           segment_generator=moteur, **options)
    return appels

def reconstruire(texte):
    # Ce que le daemon fait de `custom_lyrics` avant de chanter.
    ly = _build_lyrics_from_custom(texte, "t", "pop", 90, "Am")
    return ly, build_arrangement(ly.structure, ly.lyrics)

# Répartition réelle d'un arrangement à sections INÉGALES : c'est elle qui décide
# du débit de chaque segment, et donc de ce qui sera tronqué.
mots_par_segment = [
    sum(len(l.split()) for l in c.splitlines() if not l.strip().startswith("["))
    for c in chante(entree["inegal"], 180, decoupe)
]

ly, reconstruit = reconstruire(arrangement)
durees = (30, 120, 121, 180, 240, 300, 360, 600)

# Ce que chaque cas fait chanter, dans les deux modes : juge des refus de Klody.
cas = {}
for nom, (texte, duree) in entree["cas"].items():
    _, rec = reconstruire(texte)
    cas[nom] = {"nominal": chante(rec, duree), "decoupe": chante(rec, duree, decoupe)}

print(json.dumps({
    "segment_max": ACESTEP_MAX_SEGMENT_SEC,
    "overlap": ACESTEP_SEGMENT_OVERLAP_SEC,
    "segments_par_duree": {str(d): len(plan(d)) for d in durees},
    "segments_par_duree_decoupe": {str(d): len(plan(d, decoupe)) for d in durees},
    "sections_gardees": list(ly.lyrics),
    "reconstruit": reconstruit,
    "morceaux": chante(reconstruit, 180),
    "morceaux_decoupe": chante(reconstruit, 180, decoupe),
    "mots_par_segment_inegal": mots_par_segment,
    "cas": cas,
}))
"""

# Un texte d'un seul bloc : le mode découpé le re-chante, la passe unique le chante
# entier. C'est lui qui prouve que le refus de Klody suit le daemon dans les DEUX
# sens — et non seulement qu'il refuse quelque chose.
_UN_BLOC = " ".join(["mot"] * 480)

# Cas rejoués dans le daemon : (paroles BRUTES, durée). La sonde reçoit
# l'arrangement canonique, c'est-à-dire ce que Klody envoie vraiment.
_CAS_VERDICT = {
    "temoin": (_PAROLES_TEMOIN, 180),
    "inegal": (_SECTIONS_INEGALES, 180),
    "un_bloc": (_UN_BLOC, 240),
}

# Sonde minimale : la valeur que `config.py` du daemon calcule pour un
# environnement donné. Sert à confronter la RÈGLE de `ss.plafond_segment` (défaut
# selon la version, surcharge prioritaire), pas seulement sa valeur du jour.
# `load_dotenv` est neutralisé : le `.env` de la machine est un RÉGLAGE, pas la
# règle — il est jugé à part, par `test_le_plafond_de_segment_na_pas_bouge`.
# Sans ça, une surcharge locale rougirait les deux tests et le diagnostic
# « règle changée » vs « machine surchargée » serait impossible.
_SONDE_PLAFOND = r"""
import sys
sys.path.insert(0, ".")
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
from config import ACESTEP_MAX_SEGMENT_SEC
print(ACESTEP_MAX_SEGMENT_SEC)
"""

_VARIABLES_PLAFOND = ("ACE_STEP_VERSION", "ACESTEP_MAX_SEGMENT_SEC")


def _env_sans_plafond(**surcharges: str) -> dict[str, str]:
    """Environnement des sondes, purgé des variables du plafond.

    Le daemon tourne sous launchd (`com.klody.localsuno-daemon`), qui ne les pose
    pas : une valeur exportée dans le shell qui lance la suite ne décrit pas la
    production, elle ferait juste diverger la sonde du mode figé par conftest.
    """
    env = {k: v for k, v in os.environ.items() if k not in _VARIABLES_PLAFOND}
    return {**env, **surcharges}


def _python_localsuno() -> Path:
    if not _LOCALSUNO.is_dir():
        pytest.skip("local-suno absent de cette machine — rien à confronter")
    python = _LOCALSUNO / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"venv local-suno absent ({python})")
    return python


@pytest.fixture(scope="module")
def daemon_reel() -> dict:
    """Faits relevés dans le VRAI local-suno, ou skip explicite s'il est absent."""
    python = _python_localsuno()
    arrangement, _ = ss.canonicaliser_paroles(_PAROLES_TEMOIN)
    inegal, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
    proc = subprocess.run(
        [str(python), "-c", _SONDE],
        input=json.dumps({
            "arrangement": arrangement, "inegal": inegal,
            "plafond_decoupe": _PLAFOND_DECOUPE,
            "cas": {
                nom: [ss.canonicaliser_paroles(paroles)[0], duree]
                for nom, (paroles, duree) in _CAS_VERDICT.items()
            },
        }),
        capture_output=True, text=True, cwd=str(_LOCALSUNO), timeout=120,
        env=_env_sans_plafond(),
    )
    if proc.returncode != 0:
        pytest.fail(
            "la sonde local-suno a échoué — le contrat a peut-être changé :\n"
            + proc.stderr[-1500:]
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# Sonde des DÉBITS de chant. `song_structure` recopie deux nombres du daemon — la
# cible (2 mots/s) et le seuil d'alerte « texte trop court » (1 mot/s) — et le
# commentaire qui l'affirmait n'était relu par aucun test : si local-suno les
# changeait, Klody conseillerait des durées et lèverait des alertes pour un autre
# moteur, sans que rien ne rougisse. Le seuil est confronté par COMPORTEMENT : on
# rejoue le vrai `_warn_if_lyrics_too_short` sur une grille de débits et on
# regarde s'il avertit, plutôt que de chercher « 1.0 » dans son source.
#
# La console du daemon est remplacée APRÈS le parseur : seul ce que
# `_warn_if_lyrics_too_short` écrit est jugé.
_SONDE_DEBIT = r"""
import json, sys
sys.path.insert(0, ".")
import main
from main import _build_lyrics_from_custom, _warn_if_lyrics_too_short
from pipeline.lyrics_generator import _WORDS_PER_SEC

class Enregistreur:
    def __init__(self):
        self.lignes = []
    def print(self, *a, **k):
        self.lignes.append(" ".join(map(str, a)))
    log = print

cas = []
for arrangement, duree in json.loads(sys.stdin.read()):
    ly = _build_lyrics_from_custom(arrangement, "t", "pop", 90, "Am")
    main.console = Enregistreur()
    debit = _warn_if_lyrics_too_short(ly, duree)
    lignes = main.console.lignes
    cas.append({"debit": debit, "lignes": lignes,
                "averti": any("⚠" in l for l in lignes)})
print(json.dumps({"cible": _WORDS_PER_SEC, "cas": cas}))
"""

# Grille LITTÉRALE, jamais dérivée de `ss.DEBIT_MIN` : une grille recalculée à
# partir du seuil qu'elle surveille le suivrait dans sa dérive au lieu de la juger.
# 100 s rend le débit lisible (mots = centièmes de mot/s) et encadre le seuil
# actuel de part et d'autre, égalité stricte comprise.
_DUREE_GRILLE_DEBIT = 100
_MOTS_GRILLE_DEBIT = (50, 99, 100, 101, 120, 150)


def _texte_de(mots: int) -> str:
    return "[Couplet]\n" + " ".join(["la"] * mots)


@pytest.fixture(scope="module")
def debits_daemon() -> dict:
    """Cible et verdicts d'alerte relevés dans le VRAI local-suno."""
    python = _python_localsuno()
    cas = [
        [ss.canonicaliser_paroles(_texte_de(m))[0], _DUREE_GRILLE_DEBIT]
        for m in _MOTS_GRILLE_DEBIT
    ]
    cas.append([ss.canonicaliser_paroles(_PAROLES_TEMOIN)[0], 180])
    proc = subprocess.run(
        [str(python), "-c", _SONDE_DEBIT],
        input=json.dumps(cas), capture_output=True, text=True,
        cwd=str(_LOCALSUNO), timeout=120, env=_env_sans_plafond(),
    )
    if proc.returncode != 0:
        pytest.fail(
            "la sonde des débits a échoué — le contrat a peut-être changé :\n"
            + proc.stderr[-1500:]
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.slow
class TestPasDeDerive:
    """Confronte les constantes et le circuit au VRAI dépôt local-suno.

    Motif : `_idee_to_body` bornait la durée à 120 s en citant « bornes daemon
    (ge=10 le=120) » alors que le contrat était passé à 600 depuis longtemps.
    Toute démo au-delà de 2 min était donc silencieusement coupée de moitié, et
    rien ne pouvait rougir. Une constante recopiée sans test qui la relit est une
    constante qui ment tôt ou tard.
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

    def test_le_plafond_de_segment_na_pas_bouge(self, daemon_reel):
        # Rougi le 2026-09-27 (600.0 == 120.0) : local-suno 3fddc2c avait passé le
        # défaut v1.5 à 600 s dix-huit jours plus tôt. C'est ce test qui l'a vu.
        # Klody est comparé dans l'environnement de ses serveurs MCP (launchd, sans
        # ces variables), le daemon dans le sien, `.env` compris.
        # ⚠️ Rouge ici alors que la règle ci-dessous est verte = le `.env` de
        # local-suno surcharge le plafond sur CETTE machine : la couverture se
        # calcule alors contre un autre mode que celui du daemon.
        assert daemon_reel["segment_max"] == ss.plafond_segment({}), (
            "le daemon n'est pas dans le mode que Klody suppose"
        )
        assert daemon_reel["overlap"] == ss.SEGMENT_OVERLAP_SEC

    @pytest.mark.parametrize(
        "env",
        [
            {},
            {"ACE_STEP_VERSION": "v15"},
            {"ACE_STEP_VERSION": "v1"},
            {"ACE_STEP_VERSION": "v15", "ACESTEP_MAX_SEGMENT_SEC": "120"},
        ],
        ids=["defaut", "v15", "v1", "surcharge"],
    )
    def test_la_regle_du_plafond_est_celle_du_daemon(self, env):
        """`plafond_segment` réplique la RÈGLE du daemon, pas sa valeur du jour.

        Sans ce test, un changement du défaut v1 (ou de la version par défaut)
        passerait inaperçu tant que la machine tourne en v1.5.
        """
        python = _python_localsuno()
        proc = subprocess.run(
            [str(python), "-c", _SONDE_PLAFOND],
            env=_env_sans_plafond(**env), capture_output=True, text=True,
            cwd=str(_LOCALSUNO), timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[-1500:]
        assert float(proc.stdout.strip().splitlines()[-1]) == ss.plafond_segment(env), (
            "la règle du plafond a changé côté daemon (config.py) — "
            "réaligne song_structure.plafond_segment"
        )

    def test_le_compte_de_segments_est_celui_du_daemon(self, daemon_reel):
        calcule = {d: ss.nb_segments(int(d)) for d in daemon_reel["segments_par_duree"]}
        assert calcule == {d: n for d, n in daemon_reel["segments_par_duree"].items()}

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_le_compte_de_segments_decoupe_est_celui_du_daemon(self, daemon_reel):
        attendu = daemon_reel["segments_par_duree_decoupe"]
        assert {d: ss.nb_segments(int(d)) for d in attendu} == attendu
        assert attendu["180"] == 2, "le témoin découpé doit rester multi-segment"

    def test_le_daemon_garde_TOUTES_les_sections(self, daemon_reel):
        # Sans la numérotation des marqueurs, le 2ᵉ [Refrain] écrase le 1ᵉʳ dans le
        # dict de sections du daemon : un refrain disparaît de la chanson.
        assert len(daemon_reel["sections_gardees"]) == 5, (
            f"sections écrasées : {daemon_reel['sections_gardees']}"
        )

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_la_repartition_des_mots_est_celle_du_daemon(self, daemon_reel):
        """`debit_par_segment` doit prédire le VRAI découpage, pas une approximation.

        En mode découpé, c'est ce chiffre qui décide de la durée déduite : s'il
        diverge de ce que `split_arrangement_text` fait réellement, la durée
        choisie ne désature rien et le garde-fou devient décoratif.
        """
        arrangement, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
        predit = [m for m, _ in ss.debit_par_segment(arrangement, 180)]
        assert predit == daemon_reel["mots_par_segment_inegal"]
        assert predit[0] > predit[1], "le témoin doit rester déséquilibré"

    @pytest.mark.usefixtures("chanson_decoupee")
    def test_circuit_decoupe_chaque_segment_chante_autre_chose(self, daemon_reel):
        """Le test qui porte la conclusion du mode découpé : > 120 s sans texte re-chanté."""
        morceaux = daemon_reel["morceaux_decoupe"]
        assert len(morceaux) == ss.nb_segments(180) == 2, "180 s = 2 segments"
        assert len(set(morceaux)) == len(morceaux), (
            "des segments chanteraient le même texte"
        )

    def test_circuit_nominal_tout_le_texte_part_dans_un_appel(self, daemon_reel):
        # En une passe, rien n'est réparti : l'arrangement reconstruit ENTIER est
        # ce que le moteur reçoit. Klody doit annoncer le même nombre de segments.
        morceaux = daemon_reel["morceaux"]
        assert len(morceaux) == ss.nb_segments(180)
        if len(morceaux) == 1:
            assert morceaux[0] == daemon_reel["reconstruit"]

    @pytest.mark.parametrize("mode", ["nominal", "decoupe"])
    def test_le_verdict_de_repetition_est_celui_du_daemon(self, daemon_reel, mode, request):
        """Klody refuse « RE-CHANTERONT » SSI le daemon re-chante vraiment.

        Un refus que le daemon ne justifie pas est aussi faux qu'un refus manquant
        — c'est ce que Klody rendait du 2026-09-09 au 2026-09-27, en une passe.
        """
        if mode == "decoupe":
            request.getfixturevalue("chanson_decoupee")
        cas = {nom: c[mode] for nom, c in daemon_reel["cas"].items()}
        repete = {nom: len(set(appels)) < len(appels) for nom, appels in cas.items()}
        # Le témoin doit garder son pouvoir de discrimination dans les deux sens.
        assert repete["un_bloc"] is (mode == "decoupe")
        assert repete["temoin"] is False and repete["inegal"] is False

        for nom, (paroles, duree) in _CAS_VERDICT.items():
            r = ss.controler_couverture(paroles, duree)
            refuse = any("RE-CHANTERONT" in p for p in r["problemes"])
            assert refuse is repete[nom], (
                f"{nom} ({duree} s, mode {mode}) : le daemon "
                f"{'re-chante' if repete[nom] else 'chante tout'}, "
                f"Klody {'refuse' if refuse else 'accepte'}"
            )
            assert r["segments"] == len(cas[nom])

    def test_aucune_parole_perdue_en_route(self, daemon_reel):
        reconstruit = daemon_reel["reconstruit"]
        for ligne in _PAROLES_TEMOIN.splitlines():
            t = ligne.strip()
            if t and not t.startswith("[") and not t.lower().startswith("refrain"):
                assert t in reconstruit, f"parole perdue : {t!r}"

    def test_la_cible_de_debit_est_celle_du_daemon(self, debits_daemon):
        # `duree_conseillee`, `duree_sans_saturation` et l'alerte « débit serré »
        # reposent sur ce nombre. Côté daemon, c'est `_WORDS_PER_SEC` qui
        # dimensionne les paroles qu'il écrit lui-même.
        assert debits_daemon["cible"] == ss.DEBIT_CIBLE, (
            "la cible de débit a changé côté daemon (pipeline/lyrics_generator.py) "
            "— réaligne song_structure.DEBIT_CIBLE"
        )

    def test_le_seuil_de_texte_clairseme_est_celui_du_daemon(self, debits_daemon):
        """Klody avertit « texte clairsemé » exactement là où le daemon avertit.

        L'alerte du daemon est un `print` dans un sous-processus worker que
        personne ne lit : Klody la remonte avant le POST. Deux seuils différents
        feraient deux diagnostics contradictoires sur la même demande.
        """
        cas = debits_daemon["cas"][: len(_MOTS_GRILLE_DEBIT)]
        # « Rien entendu » n'est pas « pas d'alerte » : la sonde doit avoir capté
        # la sortie du daemon à chaque cas, sinon elle n'a rien jugé.
        assert all(c["lignes"] for c in cas), "la console du daemon n'a rien rendu"
        # Sans les deux verdicts sur la grille, la confrontation serait triviale.
        assert {c["averti"] for c in cas} == {True, False}, (
            "la grille n'encadre plus le seuil du daemon"
        )
        for mots, c in zip(_MOTS_GRILLE_DEBIT, cas, strict=True):
            rapport = ss.controler_couverture(_texte_de(mots), _DUREE_GRILLE_DEBIT)
            klody = any(a.startswith("texte clairsemé") for a in rapport["avertissements"])
            assert klody == c["averti"], (
                f"{mots} mots / {_DUREE_GRILLE_DEBIT} s : daemon "
                f"{'averti' if c['averti'] else 'muet'}, Klody "
                f"{'averti' if klody else 'muet'} — réaligne song_structure.DEBIT_MIN"
            )

    def test_le_compte_de_mots_est_celui_du_daemon(self, debits_daemon):
        # `TestComptage` affirme « même règle que _warn_if_lyrics_too_short » :
        # ici on le vérifie, sur la grille et sur le témoin à en-têtes multiples.
        durees = [_DUREE_GRILLE_DEBIT] * len(_MOTS_GRILLE_DEBIT) + [180]
        textes = [_texte_de(m) for m in _MOTS_GRILLE_DEBIT] + [_PAROLES_TEMOIN]
        for texte, duree, c in zip(textes, durees, debits_daemon["cas"], strict=True):
            arrangement, _ = ss.canonicaliser_paroles(texte)
            assert round(c["debit"] * duree) == ss.mots_chantes(arrangement)
