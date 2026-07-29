"""Non-régression du palier `expert` du bench.

Une tâche de bench a deux façons silencieuses d'être inutile, et aucune ne se voit
en lisant le code :

  - **sa fixture valide déjà** → elle mesure zéro, l'agent n'a rien à faire ;
  - **aucune solution correcte ne la passe** → elle mesure zéro aussi, et fait
    accuser le modèle.

Chaque tâche est donc encadrée par deux tests : la fixture DOIT échouer, une
solution de référence DOIT passer. S'y ajoutent les tricheries que chaque
`validate` prétend rejeter — une garde anti-triche non testée est une garde dont
on ignore si elle marche.

Aucun modèle ni gateway ici : on n'exerce que `setup()` / `validate()`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.framework import discover_tasks
from bench.tasks.expert import (
    CrossModuleRename,
    DeadlockLockOrder,
    SpecBeyondTests,
    StreamMemory,
    UnhashableDedup,
)

TACHES_EXPERT = [
    UnhashableDedup,
    DeadlockLockOrder,
    CrossModuleRename,
    SpecBeyondTests,
    StreamMemory,
]


# --------------------------------------------------------------------------- #
# Solutions de référence — ce qu'un agent compétent est censé produire.        #
# --------------------------------------------------------------------------- #


def _resoudre_unhashable_dedup(workdir: Path) -> None:
    (workdir / "records.py").write_text(
        "def dedupe(records):\n"
        '    """Dédoublonne en O(n) via une clé canonique triée."""\n'
        "    seen = set()\n"
        "    out = []\n"
        "    for r in records:\n"
        "        cle = tuple(sorted(r.items()))\n"
        "        if cle not in seen:\n"
        "            seen.add(cle)\n"
        "            out.append(r)\n"
        "    return out\n",
        encoding="utf-8",
    )


def _resoudre_deadlock(workdir: Path) -> None:
    """Unifie l'ordre d'acquisition : a puis b des deux côtés."""
    src = (workdir / "bank.py").read_text(encoding="utf-8")
    src = src.replace(
        "    # Ordre INVERSE : b puis a. C'est le bug.\n"
        "    async with lock_b:\n"
        "        await asyncio.sleep(0)\n"
        "        async with lock_a:\n",
        "    # Ordre unifié : a puis b, comme transfer_a_to_b.\n"
        "    async with lock_a:\n"
        "        await asyncio.sleep(0)\n"
        "        async with lock_b:\n",
    )
    (workdir / "bank.py").write_text(src, encoding="utf-8")


def _resoudre_cross_module_rename(workdir: Path) -> None:
    (workdir / "calc.py").write_text(
        "import warnings\n"
        "\n"
        "\n"
        "def compute_invoice_total(items):\n"
        '    """Somme quantité × prix unitaire, arrondie au centime."""\n'
        "    return round(sum(i['qty'] * i['unit_price'] for i in items), 2)\n"
        "\n"
        "\n"
        "def compute_total(items):\n"
        '    """Alias déprécié de compute_invoice_total."""\n'
        "    warnings.warn(\n"
        "        'compute_total est déprécié, utilise compute_invoice_total',\n"
        "        DeprecationWarning,\n"
        "        stacklevel=2,\n"
        "    )\n"
        "    return compute_invoice_total(items)\n",
        encoding="utf-8",
    )
    (workdir / "billing.py").write_text(
        "from calc import compute_invoice_total\n"
        "\n"
        "\n"
        "def invoice(items, tax_rate=0.2):\n"
        "    ht = compute_invoice_total(items)\n"
        "    return round(ht * (1 + tax_rate), 2)\n",
        encoding="utf-8",
    )
    (workdir / "report.py").write_text(
        "import calc\n"
        "\n"
        "\n"
        "def summary(orders):\n"
        "    return {\n"
        "        name: calc.compute_invoice_total(items) for name, items in orders.items()\n"
        "    }\n",
        encoding="utf-8",
    )


def _resoudre_spec_beyond_tests(workdir: Path) -> None:
    """Remplace le corps SANS toucher au docstring : c'est la spec, et `validate`
    vérifie qu'il n'a pas bougé. On coupe donc au `\"\"\"` fermant et on réécrit
    tout ce qui suit."""
    src = (workdir / "duration.py").read_text(encoding="utf-8")
    entete, sep, _ancien_corps = src.partition('    """\n')
    assert sep, "docstring de parse_duration introuvable"
    entete = entete.replace(
        "_UNITES = {'s': 1, 'm': 60, 'h': 3600}\n",
        "_UNITES = {'s': 1, 'm': 60, 'h': 3600}\n"
        # \Z et non $ : `$` accepterait un saut de ligne final, donc '5m\n'.
        "_COMPOSE = re.compile(r'^(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?\\Z')\n",
    )
    corps = (
        "    if not isinstance(texte, str) or not texte:\n"
        "        raise ValueError('durée vide ou non textuelle: %r' % (texte,))\n"
        "    m = _COMPOSE.match(texte)\n"
        "    if m is None or not any(m.groups()):\n"
        "        raise ValueError('durée invalide: %r' % (texte,))\n"
        "    h, mi, s = (int(g) if g else 0 for g in m.groups())\n"
        "    return h * 3600 + mi * 60 + s\n"
    )
    (workdir / "duration.py").write_text(entete + sep + corps, encoding="utf-8")


def _resoudre_stream_memory(workdir: Path) -> None:
    (workdir / "readings.py").write_text(
        "def iter_numbers(path):\n"
        '    """Rend les valeurs une à une, sans jamais tout matérialiser."""\n'
        "    with open(path) as f:\n"
        "        for line in f:\n"
        "            if line.strip():\n"
        "                yield int(line)\n"
        "\n"
        "\n"
        "def total(path):\n"
        '    """Somme des valeurs du fichier, en flux."""\n'
        "    return sum(iter_numbers(path))\n",
        encoding="utf-8",
    )


SOLUTIONS = {
    UnhashableDedup: _resoudre_unhashable_dedup,
    DeadlockLockOrder: _resoudre_deadlock,
    CrossModuleRename: _resoudre_cross_module_rename,
    SpecBeyondTests: _resoudre_spec_beyond_tests,
    StreamMemory: _resoudre_stream_memory,
}


# --------------------------------------------------------------------------- #
# Enregistrement                                                              #
# --------------------------------------------------------------------------- #


class TestEnregistrement:
    def test_les_taches_expert_sont_decouvertes(self):
        """Un fichier de tâches oublié dans l'import ne se voit qu'à l'exécution
        du bench, des minutes plus tard."""
        trouvees = discover_tasks()
        for cls in TACHES_EXPERT:
            assert cls.id in trouvees, f"{cls.id} absente du registre"
            assert trouvees[cls.id] is cls

    @pytest.mark.parametrize("cls", TACHES_EXPERT, ids=lambda c: c.id)
    def test_categorie_et_prompt(self, cls):
        assert cls.category == "expert"
        assert cls.id.startswith("expert/")
        assert len(cls.prompt) > 200, "un énoncé trop court sous-spécifie la tâche"

    def test_cli_refuse_une_categorie_inconnue(self):
        """`--category` a une liste FERMÉE, et c'est ce qui rend le test
        précédent nécessaire : ajouter des tâches sans ouvrir la liste rendrait
        le palier injoignable en ligne de commande, avec un message d'argparse
        qui accuserait l'utilisateur."""
        from bench.run import main

        with pytest.raises(SystemExit) as exc:
            main(["--category", "legendaire", "--dry-run"])
        assert exc.value.code == 2

    def test_dry_run_expert_ne_selectionne_que_expert(self, capsys):
        from bench.run import main

        assert main(["--category", "expert", "--dry-run"]) == 0
        sortie = capsys.readouterr().out
        assert f"{len(TACHES_EXPERT)} tâche(s) sélectionnée(s)" in sortie
        assert "[easy]" not in sortie and "[hard]" not in sortie


# --------------------------------------------------------------------------- #
# Le contrat central : fixture rouge, solution verte                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls", TACHES_EXPERT, ids=lambda c: c.id)
def test_la_fixture_echoue(cls, tmp_path):
    """Sans ça, une tâche pourrait valider avant que l'agent ait touché quoi que
    ce soit — et compter comme un succès dans le score."""
    task = cls()
    task.setup(tmp_path)
    ok, detail = task.validate(tmp_path)
    assert ok is False, f"{cls.id} : la fixture valide déjà — la tâche ne mesure rien"
    assert detail, "un échec sans message ne se diagnostique pas"


@pytest.mark.parametrize("cls", TACHES_EXPERT, ids=lambda c: c.id)
def test_la_solution_de_reference_passe(cls, tmp_path):
    """Le pendant du test précédent : une tâche que personne ne peut passer
    mesure zéro elle aussi, en accusant le modèle."""
    task = cls()
    task.setup(tmp_path)
    SOLUTIONS[cls](tmp_path)
    ok, detail = task.validate(tmp_path)
    assert ok is True, f"{cls.id} : la solution de référence échoue — {detail}"


# --------------------------------------------------------------------------- #
# Les gardes anti-triche, une par une                                         #
# --------------------------------------------------------------------------- #


class TestGardesAntiTriche:
    def test_dedup_resultat_code_en_dur(self, tmp_path):
        task = UnhashableDedup()
        task.setup(tmp_path)
        (tmp_path / "records.py").write_text(
            "def dedupe(records):\n    return []\n", encoding="utf-8"
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "constante" in detail or "pytest" in detail

    def test_dedup_spec_modifiee(self, tmp_path):
        task = UnhashableDedup()
        task.setup(tmp_path)
        _resoudre_unhashable_dedup(tmp_path)
        (tmp_path / "test_records.py").write_text(
            "def test_bidon():\n    assert True\n", encoding="utf-8"
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False and "modifié" in detail

    def test_deadlock_verrou_supprime(self, tmp_path):
        """Ne garder qu'un verrou fait passer le test — en supprimant le
        problème au lieu de l'ordonnancer."""
        task = DeadlockLockOrder()
        task.setup(tmp_path)
        src = (tmp_path / "bank.py").read_text(encoding="utf-8")
        src = src.replace("lock_b = asyncio.Lock()", "lock_b = lock_a")
        src = src.replace("async with lock_b:\n            balances", "if True:\n            balances")
        (tmp_path / "bank.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "Lock" in detail

    def test_deadlock_entrelacement_supprime(self, tmp_path):
        """Retirer le sleep(0) fait disparaître l'entrelacement : le test passe
        sans que l'ordre ait été corrigé."""
        task = DeadlockLockOrder()
        task.setup(tmp_path)
        src = (tmp_path / "bank.py").read_text(encoding="utf-8")
        src = re.sub(r" *await asyncio\.sleep\(0\).*\n", "", src)
        (tmp_path / "bank.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "asyncio.sleep" in detail

    def test_deadlock_soldes_en_dur(self, tmp_path):
        task = DeadlockLockOrder()
        task.setup(tmp_path)
        _resoudre_deadlock(tmp_path)
        src = (tmp_path / "bank.py").read_text(encoding="utf-8")
        src = src.replace("    return dict(balances)", "    return {'a': 80, 'b': 120}")
        (tmp_path / "bank.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "littéral" in detail

    def test_rename_alias_sans_propagation(self, tmp_path):
        """LE point de la tâche : ajouter l'alias suffit à rendre les tests
        verts. Sans cette garde, le renommage pourrait n'avoir jamais eu lieu
        chez les appelants."""
        task = CrossModuleRename()
        task.setup(tmp_path)
        _resoudre_cross_module_rename(tmp_path)
        # On remet billing.py dans son état d'origine : il appelle l'alias.
        (tmp_path / "billing.py").write_text(
            "from calc import compute_total\n"
            "\n"
            "\n"
            "def invoice(items, tax_rate=0.2):\n"
            "    ht = compute_total(items)\n"
            "    return round(ht * (1 + tax_rate), 2)\n",
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "billing.py" in detail and "compute_total" in detail

    def test_rename_alias_duplique_le_corps(self, tmp_path):
        task = CrossModuleRename()
        task.setup(tmp_path)
        _resoudre_cross_module_rename(tmp_path)
        src = (tmp_path / "calc.py").read_text(encoding="utf-8")
        src = src.replace(
            "    return compute_invoice_total(items)",
            "    return round(sum(i['qty'] * i['unit_price'] for i in items), 2)",
        )
        (tmp_path / "calc.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "délègue" in detail

    def test_rename_alias_supprime(self, tmp_path):
        task = CrossModuleRename()
        task.setup(tmp_path)
        _resoudre_cross_module_rename(tmp_path)
        src = (tmp_path / "calc.py").read_text(encoding="utf-8")
        src = src[: src.index("def compute_total(items):")]
        (tmp_path / "calc.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "alias" in detail

    def test_spec_docstring_reecrit(self, tmp_path):
        """Réécrire la spec pour coller au code, c'est déplacer la cible."""
        task = SpecBeyondTests()
        task.setup(tmp_path)
        src = (tmp_path / "duration.py").read_text(encoding="utf-8")
        src = src.replace("      2. durée composée, unités décroissantes et sans séparateur :\n", "")
        src = src.replace("         '1h30m' -> 5400, '2h5m30s' -> 7530, '10m30s' -> 630\n", "")
        (tmp_path / "duration.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "spec" in detail

    def test_spec_composee_seule_ne_suffit_pas(self, tmp_path):
        """Gérer la composition sans lever sur l'invalide reste hors spec :
        les deux moitiés comptent."""
        task = SpecBeyondTests()
        task.setup(tmp_path)
        _resoudre_spec_beyond_tests(tmp_path)
        src = (tmp_path / "duration.py").read_text(encoding="utf-8")
        src = src.replace(
            "        raise ValueError('durée invalide: %r' % (texte,))",
            "        return None",
        )
        (tmp_path / "duration.py").write_text(src, encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False and "hors spec" in detail

    @pytest.mark.parametrize(
        "remplacement",
        [
            "        return sum(int(x) for x in f.read().split())",
            "        return sum(int(x) for x in f.readlines() if x.strip())",
            "        return sum(int(x) for x in list(f) if x.strip())",
        ],
        ids=["read", "readlines", "list"],
    )
    def test_stream_lecture_globale_interdite(self, tmp_path, remplacement):
        task = StreamMemory()
        task.setup(tmp_path)
        (tmp_path / "readings.py").write_text(
            "def total(path):\n    with open(path) as f:\n" + remplacement + "\n",
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False and "interdit" in detail

    def test_stream_liste_intermediaire_depasse_le_plafond(self, tmp_path):
        """Sans `.read()` ni `.readlines()`, une compréhension de liste passe les
        gardes syntaxiques — c'est la mesure `tracemalloc` qui la rejette."""
        task = StreamMemory()
        task.setup(tmp_path)
        (tmp_path / "readings.py").write_text(
            "def total(path):\n"
            "    with open(path) as f:\n"
            "        return sum([int(line) for line in f if line.strip()])\n",
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False and "crête" in detail
