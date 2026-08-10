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
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from klody_mcp import song_structure as ss

_LOCALSUNO = Path.home() / "local-suno"


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

    def test_moins_de_sections_que_de_segments_est_refuse(self):
        # 240 s = 3 segments ; 2 sections ⇒ le 3ᵉ segment re-chante le 2ᵉ.
        r = ss.controler_couverture(_paroles(2, 240), 240)
        assert r["couvrable"] is False
        assert "RE-CHANTERONT" in r["problemes"][0]
        assert r["sections_min"] == 3

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
# répartition entre segments. C'est ce que `generate_song_long` chanterait.
#
# ⚠️ Pourquoi un sous-processus et pas un `sys.path.append` : les DEUX dépôts ont
# un module `config` (et un `main`). Importé en cours de suite, `config` est déjà
# celui de klody-code-ai dans `sys.modules`, et `pipeline.acestep_generator` échoue
# sur `ACE_STEP_SEED`. Écrit en import direct, ce garde-fou se SAUTAIT donc en
# silence sur la machine même où il devait mordre — un test sauté est
# indiscernable d'un test vert.
_SONDE = r"""
import json, sys
sys.path.insert(0, ".")
from config import ACESTEP_MAX_SEGMENT_SEC, ACESTEP_SEGMENT_OVERLAP_SEC
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
    """Faits relevés dans le VRAI local-suno, ou skip explicite s'il est absent."""
    if not _LOCALSUNO.is_dir():
        pytest.skip("local-suno absent de cette machine — rien à confronter")
    python = _LOCALSUNO / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"venv local-suno absent ({python})")
    arrangement, _ = ss.canonicaliser_paroles(_PAROLES_TEMOIN)
    inegal, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
    proc = subprocess.run(
        [str(python), "-c", _SONDE],
        input=json.dumps({"arrangement": arrangement, "inegal": inegal}),
        capture_output=True, text=True, cwd=str(_LOCALSUNO), timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            "la sonde local-suno a échoué — le contrat a peut-être changé :\n"
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
        assert daemon_reel["segment_max"] == ss.SEGMENT_MAX_SEC
        assert daemon_reel["overlap"] == ss.SEGMENT_OVERLAP_SEC

    def test_le_compte_de_segments_est_celui_du_daemon(self, daemon_reel):
        calcule = {d: ss.nb_segments(int(d)) for d in daemon_reel["segments_par_duree"]}
        assert calcule == {d: n for d, n in daemon_reel["segments_par_duree"].items()}

    def test_le_daemon_garde_TOUTES_les_sections(self, daemon_reel):
        # Sans la numérotation des marqueurs, le 2ᵉ [Refrain] écrase le 1ᵉʳ dans le
        # dict de sections du daemon : un refrain disparaît de la chanson.
        assert len(daemon_reel["sections_gardees"]) == 5, (
            f"sections écrasées : {daemon_reel['sections_gardees']}"
        )

    def test_la_repartition_des_mots_est_celle_du_daemon(self, daemon_reel):
        """`debit_par_segment` doit prédire le VRAI découpage, pas une approximation.

        C'est ce chiffre qui décide désormais de la durée déduite : s'il diverge
        de ce que `split_arrangement_text` fait réellement, la durée choisie ne
        désature rien et le garde-fou devient décoratif.
        """
        arrangement, _ = ss.canonicaliser_paroles(_SECTIONS_INEGALES)
        predit = [m for m, _ in ss.debit_par_segment(arrangement, 180)]
        assert predit == daemon_reel["mots_par_segment_inegal"]
        assert predit[0] > predit[1], "le témoin doit rester déséquilibré"

    def test_circuit_complet_chaque_segment_chante_autre_chose(self, daemon_reel):
        """Le test qui porte la conclusion : > 120 s sans texte re-chanté."""
        morceaux = daemon_reel["morceaux"]
        assert len(morceaux) == 2, "180 s = 2 segments"
        assert len(set(morceaux)) == len(morceaux), (
            "des segments chanteraient le même texte"
        )

    def test_aucune_parole_perdue_en_route(self, daemon_reel):
        reconstruit = daemon_reel["reconstruit"]
        for ligne in _PAROLES_TEMOIN.splitlines():
            t = ligne.strip()
            if t and not t.startswith("[") and not t.lower().startswith("refrain"):
                assert t in reconstruit, f"parole perdue : {t!r}"
