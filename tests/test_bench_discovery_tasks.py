"""Non-régression du palier `discovery` du bench.

Même contrat que `tests/test_bench_expert_tasks.py` — fixture rouge, solution de
référence verte, gardes anti-triche éprouvées une par une — plus une propriété
qui n'appartient qu'à ce palier :

    **la contrainte décisive doit être DANS le dossier et ABSENTE de l'énoncé.**

C'est ce qui sépare une tâche de recherche d'une devinette. Si le fait migrait
dans l'énoncé, la tâche cesserait de mesurer la découverte sans que rien
n'échoue ; s'il n'était nulle part, elle mesurerait la chance. Les deux moitiés
sont donc testées, et dans les deux sens.

Aucun modèle ni gateway : on n'exerce que `setup()` / `validate()`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.framework import discover_tasks
from bench.tasks.discovery import (
    ConfigPrecedence,
    DataContract,
    ErrorContract,
    HiddenInvariant,
)

TACHES_DISCOVERY = [HiddenInvariant, ConfigPrecedence, ErrorContract, DataContract]


# --------------------------------------------------------------------------- #
# Solutions de référence                                                      #
# --------------------------------------------------------------------------- #


def _resoudre_hidden_invariant(workdir: Path) -> None:
    src = (workdir / "cache.py").read_text(encoding="utf-8")
    (workdir / "cache.py").write_text(
        "import copy\n"
        "\n"
        "\n" + src.replace(
            "    def get(self, key, default=None):",
            "    def set_many(self, mapping):\n"
            "        for key, value in mapping.items():\n"
            "            if not isinstance(key, str):\n"
            "                raise TypeError('clé non str: %r' % (key,))\n"
            "        for key, value in mapping.items():\n"
            "            # Incident 2026-03 : jamais l'objet de l'appelant.\n"
            "            self._data[key] = copy.deepcopy(value)\n"
            "\n"
            "    def get(self, key, default=None):",
        ),
        encoding="utf-8",
    )


_SETTINGS_REFERENCE = (
    "import json\n"
    "import os\n"
    "from pathlib import Path\n"
    "\n"
    "DEFAULT_TIMEOUT = 30\n"
    "CONFIG_PATH = Path(__file__).with_name('settings.json')\n"
    "\n"
    "\n"
    "def _entier(valeur, provenance):\n"
    "    try:\n"
    "        return int(valeur)\n"
    "    except (TypeError, ValueError):\n"
    "        raise ValueError(\n"
    "            '%s: valeur non entière %r' % (provenance, valeur)\n"
    "        ) from None\n"
    "\n"
    "\n"
    "def load_settings(argv=None):\n"
    "    valeurs = {'timeout': DEFAULT_TIMEOUT}\n"
    "    if CONFIG_PATH.exists():\n"
    "        fichier = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
    "        if 'timeout' in fichier:\n"
    "            valeurs['timeout'] = fichier['timeout']\n"
    "    env = os.environ.get('APP_TIMEOUT')\n"
    "    if env is not None and env.strip():\n"
    "        valeurs['timeout'] = _entier(env.strip(), 'APP_TIMEOUT')\n"
    "    args = list(argv or [])\n"
    "    if '--timeout' in args:\n"
    "        i = args.index('--timeout')\n"
    "        if i + 1 < len(args):\n"
    "            valeurs['timeout'] = _entier(args[i + 1], '--timeout')\n"
    "    return valeurs\n"
)


def _resoudre_config_precedence(workdir: Path) -> None:
    (workdir / "settings.py").write_text(_SETTINGS_REFERENCE, encoding="utf-8")


_INI_REFERENCE = (
    "from errors import ParseError\n"
    "\n"
    "\n"
    "def parse_ini(texte):\n"
    "    sortie = {}\n"
    "    section = None\n"
    "    for numero, ligne in enumerate(texte.splitlines(), start=1):\n"
    "        nu = ligne.strip()\n"
    "        if not nu or nu.startswith('#'):\n"
    "            continue\n"
    "        if nu.startswith('[') and nu.endswith(']'):\n"
    "            nom = nu[1:-1].strip()\n"
    "            if not nom:\n"
    "                raise ParseError('section sans nom', line=numero)\n"
    "            section = nom\n"
    "            sortie.setdefault(section, {})\n"
    "            continue\n"
    "        if '=' not in nu:\n"
    "            raise ParseError('ni section ni affectation', line=numero)\n"
    "        if section is None:\n"
    "            raise ParseError('entrée hors section', line=numero)\n"
    "        cle, _, val = nu.partition('=')\n"
    "        sortie[section][cle.strip()] = val.strip()\n"
    "    return sortie\n"
)


def _resoudre_error_contract(workdir: Path) -> None:
    (workdir / "ini_reader.py").write_text(_INI_REFERENCE, encoding="utf-8")


_LEDGER_REFERENCE = (
    "def _montant(brut):\n"
    "    if brut is None:\n"
    "        return 0.0\n"
    "    if isinstance(brut, str):\n"
    "        brut = brut.replace('$', '').replace(',', '').strip()\n"
    "    return float(brut)\n"
    "\n"
    "\n"
    "def total(records):\n"
    "    return round(sum(_montant(r.get('amount')) for r in records), 2)\n"
)


def _resoudre_data_contract(workdir: Path) -> None:
    (workdir / "ledger.py").write_text(_LEDGER_REFERENCE, encoding="utf-8")


SOLUTIONS = {
    HiddenInvariant: _resoudre_hidden_invariant,
    ConfigPrecedence: _resoudre_config_precedence,
    ErrorContract: _resoudre_error_contract,
    DataContract: _resoudre_data_contract,
}


# --------------------------------------------------------------------------- #
# Enregistrement et câblage                                                   #
# --------------------------------------------------------------------------- #


class TestEnregistrement:
    def test_les_taches_sont_decouvertes(self):
        trouvees = discover_tasks()
        for cls in TACHES_DISCOVERY:
            assert cls.id in trouvees, f"{cls.id} absente du registre"
            assert trouvees[cls.id] is cls

    @pytest.mark.parametrize("cls", TACHES_DISCOVERY, ids=lambda c: c.id)
    def test_categorie_et_prompt(self, cls):
        assert cls.category == "discovery"
        assert cls.id.startswith("discovery/")
        assert len(cls.prompt) > 200

    def test_dry_run_ne_selectionne_que_discovery(self, capsys):
        from bench.run import main

        assert main(["--category", "discovery", "--dry-run"]) == 0
        sortie = capsys.readouterr().out
        assert f"{len(TACHES_DISCOVERY)} tâche(s) sélectionnée(s)" in sortie
        for autre in ("[easy]", "[hard]", "[expert]"):
            assert autre not in sortie

    def test_router_eval_projette_discovery_sur_hard(self):
        """Le Router ne peut pas prédire « discovery » : sans projection, chaque
        tâche compterait comme une erreur du Router et ferait chuter son F1."""
        from bench.router_eval import _attendu

        assert _attendu("discovery") == "hard"
        assert _attendu("expert") == "hard"
        assert _attendu("easy") == "easy"


# --------------------------------------------------------------------------- #
# La propriété propre au palier : trouvable dans le dossier, absente du texte  #
# --------------------------------------------------------------------------- #


# (tâche, fait décisif tel qu'il est écrit dans le dossier, mots que l'énoncé
#  ne doit PAS contenir sous peine de donner la réponse)
FAITS_DECISIFS = [
    (HiddenInvariant, "copie profonde", ("deepcopy", "copie profonde", "copy")),
    (ConfigPrecedence, "`APP_TIMEOUT` vide vaut ABSENTE", ("priorité", "vide vaut", "gagne")),
    (ErrorContract, "lève **`ParseError`**", ("ParseError", "AppError", "line=")),
    (DataContract, "$1,250.00", ("$", "virgule", "coercition")),
]


@pytest.mark.parametrize(
    ("cls", "fait", "interdits"), FAITS_DECISIFS, ids=lambda v: getattr(v, "id", "")
)
def test_le_fait_decisif_est_dans_le_dossier(cls, fait, interdits, tmp_path):
    """Sans ça, la tâche mesurerait la chance : on ne peut pas reprocher à un
    agent d'ignorer une contrainte qui n'est écrite nulle part."""
    cls().setup(tmp_path)
    trouve = any(
        fait in p.read_text(encoding="utf-8", errors="ignore")
        for p in tmp_path.rglob("*")
        if p.is_file()
    )
    assert trouve, f"{cls.id} : « {fait} » n'est dans AUCUN fichier du dossier"


@pytest.mark.parametrize(
    ("cls", "fait", "interdits"), FAITS_DECISIFS, ids=lambda v: getattr(v, "id", "")
)
def test_l_enonce_ne_donne_pas_la_reponse(cls, fait, interdits):
    """Le pendant du test précédent. Si le fait migrait dans l'énoncé, la tâche
    cesserait de mesurer la découverte — et rien n'échouerait pour le signaler."""
    for mot in interdits:
        assert mot not in cls.prompt, f"{cls.id} : l'énoncé contient « {mot} »"


@pytest.mark.parametrize("cls", TACHES_DISCOVERY, ids=lambda c: c.id)
def test_l_enonce_previent_qu_il_faut_chercher(cls):
    """Une tâche de recherche s'annonce. Sinon elle punit un agent qui a
    correctement fait ce qu'on lui demandait."""
    assert "NE SONT PAS écrites dans cet énoncé" in cls.prompt
    assert "Explore" in cls.prompt


# --------------------------------------------------------------------------- #
# Le contrat central                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls", TACHES_DISCOVERY, ids=lambda c: c.id)
def test_la_fixture_echoue(cls, tmp_path):
    task = cls()
    task.setup(tmp_path)
    ok, detail = task.validate(tmp_path)
    assert ok is False, f"{cls.id} : la fixture valide déjà — la tâche ne mesure rien"
    assert detail


@pytest.mark.parametrize("cls", TACHES_DISCOVERY, ids=lambda c: c.id)
def test_la_solution_de_reference_passe(cls, tmp_path):
    task = cls()
    task.setup(tmp_path)
    SOLUTIONS[cls](tmp_path)
    ok, detail = task.validate(tmp_path)
    assert ok is True, f"{cls.id} : la solution de référence échoue — {detail}"


# --------------------------------------------------------------------------- #
# L'implémentation « raisonnable mais ignorante » — le cœur du palier          #
# --------------------------------------------------------------------------- #


class TestImplementationIgnorante:
    """Chaque cas est du code que personne ne relèverait en revue, écrit par
    quelqu'un qui n'a pas ouvert les autres fichiers. C'est exactement ce que
    le palier doit rejeter — si l'un passait, la tâche ne mesurerait rien."""

    def _ecrire_set_many(self, tmp_path: Path, corps: str) -> None:
        src = (tmp_path / "cache.py").read_text(encoding="utf-8")
        (tmp_path / "cache.py").write_text(
            src.replace(
                "    def get(self, key, default=None):",
                "    def set_many(self, mapping):\n"
                + corps
                + "\n    def get(self, key, default=None):",
            ),
            encoding="utf-8",
        )

    def test_set_many_alias_le_dict_entier(self, tmp_path):
        """`self._data = mapping` : le cache DEVIENT le dict de l'appelant. C'est
        littéralement l'API `set_dict` de l'incident 2026-03."""
        task = HiddenInvariant()
        task.setup(tmp_path)
        self._ecrire_set_many(tmp_path, "        self._data = mapping\n")
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "2026-03" in detail, detail

    def test_set_many_sans_copie_profonde(self, tmp_path):
        """Déléguer à `self.set` en boucle. Le plus naturel, et le plus subtil :
        itérer par clé écarte DÉJÀ l'aliasing du dict externe, si bien que le
        seul symptôme restant est la valeur imbriquée partagée — exactement le
        cas que le journal dit avoir mis trois jours à trouver."""
        task = HiddenInvariant()
        task.setup(tmp_path)
        self._ecrire_set_many(
            tmp_path,
            "        for key, value in mapping.items():\n"
            "            self.set(key, value)\n",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "partagée" in detail, detail

    def test_set_many_copie_superficielle_explicite(self, tmp_path):
        """`copy.copy` au lieu de `copy.deepcopy` : la distinction que le journal
        prend soin d'écrire, et la seule que la mesure peut vérifier."""
        task = HiddenInvariant()
        task.setup(tmp_path)
        src = (tmp_path / "cache.py").read_text(encoding="utf-8")
        (tmp_path / "cache.py").write_text("import copy\n\n\n" + src, encoding="utf-8")
        self._ecrire_set_many(
            tmp_path,
            "        for key, value in mapping.items():\n"
            "            self.set(key, copy.copy(value))\n",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "superficielle" in detail, detail

    def test_set_many_sans_controle_de_cle(self, tmp_path):
        """Copie profonde correcte, mais clés non vérifiées : `set_many` écrit
        dans `_data` sans passer par `set`, donc le garde-fou de `set` ne
        s'applique plus. Incident 2026-05."""
        task = HiddenInvariant()
        task.setup(tmp_path)
        src = (tmp_path / "cache.py").read_text(encoding="utf-8")
        (tmp_path / "cache.py").write_text("import copy\n\n\n" + src, encoding="utf-8")
        self._ecrire_set_many(
            tmp_path,
            "        for key, value in mapping.items():\n"
            "            self._data[key] = copy.deepcopy(value)\n",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "2026-05" in detail, detail

    def test_config_env_lue_par_veracite(self, tmp_path):
        """`if os.environ.get('APP_TIMEOUT'):` puis `int(...)` : la forme la plus
        courante. Elle rate l'env vide par chance et le `--timeout 0` par
        construction."""
        task = ConfigPrecedence()
        task.setup(tmp_path)
        (tmp_path / "settings.py").write_text(
            _SETTINGS_REFERENCE.replace(
                "    if '--timeout' in args:\n"
                "        i = args.index('--timeout')\n"
                "        if i + 1 < len(args):\n"
                "            valeurs['timeout'] = _entier(args[i + 1], '--timeout')\n",
                "    for i, a in enumerate(args):\n"
                "        if a == '--timeout' and i + 1 < len(args):\n"
                "            cli = _entier(args[i + 1], '--timeout')\n"
                "            if cli:\n"
                "                valeurs['timeout'] = cli\n",
            ),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "zéro" in detail, detail

    def test_config_env_vide_lue_comme_zero(self, tmp_path):
        task = ConfigPrecedence()
        task.setup(tmp_path)
        (tmp_path / "settings.py").write_text(
            _SETTINGS_REFERENCE.replace(
                "    if env is not None and env.strip():",
                "    if env is not None:",
            ),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "vide" in detail, detail

    def test_config_ordre_de_priorite_invente(self, tmp_path):
        """Fichier prioritaire sur la CLI : plausible, et contraire au README."""
        task = ConfigPrecedence()
        task.setup(tmp_path)
        (tmp_path / "settings.py").write_text(
            _SETTINGS_REFERENCE.replace(
                "def load_settings(argv=None):\n"
                "    valeurs = {'timeout': DEFAULT_TIMEOUT}\n"
                "    if CONFIG_PATH.exists():",
                "def load_settings(argv=None):\n"
                "    valeurs = {'timeout': DEFAULT_TIMEOUT}\n"
                "    if False:",
            ).replace(
                "    return valeurs\n",
                "    if CONFIG_PATH.exists():\n"
                "        fichier = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
                "        if 'timeout' in fichier:\n"
                "            valeurs['timeout'] = fichier['timeout']\n"
                "    return valeurs\n",
            ),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "cli > env" in detail or "env > fichier" in detail, detail

    def test_ini_leve_valueerror(self, tmp_path):
        """Le réflexe Python correct — et interdit par CONVENTIONS.md."""
        task = ErrorContract()
        task.setup(tmp_path)
        (tmp_path / "ini_reader.py").write_text(
            _INI_REFERENCE.replace("from errors import ParseError\n", "")
            .replace("ParseError('section sans nom', line=numero)", "ValueError('section sans nom')")
            .replace(
                "ParseError('ni section ni affectation', line=numero)",
                "ValueError('ligne invalide')",
            )
            .replace(
                "ParseError('entrée hors section', line=numero)",
                "ValueError('entrée hors section')",
            ),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "native" in detail, detail

    def test_ini_parseerror_sans_ligne(self, tmp_path):
        """`ParseError` sans `line=` : `errors.py` l'exige en argument nommé, donc
        l'omettre lève TypeError — que la sonde signale comme exception native."""
        task = ErrorContract()
        task.setup(tmp_path)
        (tmp_path / "ini_reader.py").write_text(
            _INI_REFERENCE.replace(", line=numero", ", line=None"),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "line=" in detail, detail

    def test_ini_ligne_indexee_a_zero(self, tmp_path):
        """Le décalage classique. CONVENTIONS.md écrit « 1-indexé » en toutes
        lettres, et le voisin de référence le fait."""
        task = ErrorContract()
        task.setup(tmp_path)
        (tmp_path / "ini_reader.py").write_text(
            _INI_REFERENCE.replace("start=1", "start=0"),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "line=" in detail, detail

    def test_ini_lignes_vides_non_comptees(self, tmp_path):
        """Numéroter après filtrage des blancs : le numéro ne désigne alors plus
        la ligne du fichier, ce qui est tout l'intérêt du champ."""
        task = ErrorContract()
        task.setup(tmp_path)
        (tmp_path / "ini_reader.py").write_text(
            _INI_REFERENCE.replace(
                "    for numero, ligne in enumerate(texte.splitlines(), start=1):\n"
                "        nu = ligne.strip()\n"
                "        if not nu or nu.startswith('#'):\n"
                "            continue\n",
                "    utiles = [x for x in texte.splitlines() if x.strip()]\n"
                "    for numero, ligne in enumerate(utiles, start=1):\n"
                "        nu = ligne.strip()\n"
                "        if nu.startswith('#'):\n"
                "            continue\n",
            ),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "line=" in detail, detail

    def test_ledger_somme_naive(self, tmp_path):
        """`sum(r['amount'] for r in records)` — l'énoncé le suggère, les données
        l'interdisent."""
        task = DataContract()
        task.setup(tmp_path)
        (tmp_path / "ledger.py").write_text(
            "def total(records):\n"
            "    return round(sum(r['amount'] for r in records), 2)\n",
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "levée" in detail, detail

    def test_ledger_oublie_dollar_et_virgules(self, tmp_path):
        """Coercition partielle : `float(str(x))` gère '18.40' et casse sur
        '$1,250.00'. C'est le cas qu'on ne voit qu'en ouvrant le fichier."""
        task = DataContract()
        task.setup(tmp_path)
        (tmp_path / "ledger.py").write_text(
            "def total(records):\n"
            "    somme = 0.0\n"
            "    for r in records:\n"
            "        brut = r.get('amount')\n"
            "        if brut is None:\n"
            "            continue\n"
            "        somme += float(brut)\n"
            "    return round(somme, 2)\n",
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "levée" in detail, detail

    def test_ledger_montants_negatifs_absolus(self, tmp_path):
        """`abs()` sur les montants : le total grossit, aucune exception."""
        task = DataContract()
        task.setup(tmp_path)
        (tmp_path / "ledger.py").write_text(
            _LEDGER_REFERENCE.replace("return float(brut)", "return abs(float(brut))"),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "attendu" in detail, detail


class TestGardesDeDocumentation:
    """La documentation que l'agent doit lire est la cible. La réécrire pour
    coller à son code serait déplacer la cible — chaque tâche le refuse."""

    @pytest.mark.parametrize(
        ("cls", "resoudre", "fichier", "retire"),
        [
            (HiddenInvariant, _resoudre_hidden_invariant, "docs/INCIDENTS.md", "copie profonde"),
            (
                ConfigPrecedence,
                _resoudre_config_precedence,
                "README.md",
                "`--timeout 0` vaut ZÉRO",
            ),
            (ErrorContract, _resoudre_error_contract, "docs/CONVENTIONS.md", "1-indexé"),
        ],
        ids=["incidents", "readme", "conventions"],
    )
    def test_doc_reecrite_refusee(self, cls, resoudre, fichier, retire, tmp_path):
        task = cls()
        task.setup(tmp_path)
        resoudre(tmp_path)
        chemin = tmp_path / fichier
        chemin.write_text(
            chemin.read_text(encoding="utf-8").replace(retire, "(supprimé)"),
            encoding="utf-8",
        )
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "modifié" in detail, detail

    def test_donnees_modifiees_refusees(self, tmp_path):
        """Réécrire les données pour qu'elles rentrent dans un `sum()` naïf est la
        triche évidente de `data_contract` — les données SONT la spec."""
        task = DataContract()
        task.setup(tmp_path)
        _resoudre_data_contract(tmp_path)
        chemin = tmp_path / "data" / "transactions.json"
        recs = json.loads(chemin.read_text(encoding="utf-8"))
        for r in recs:
            r["amount"] = 1
        chemin.write_text(json.dumps(recs), encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "modifié" in detail, detail

    def test_module_voisin_protege(self, tmp_path):
        """`csv_reader.py` est l'implémentation de référence citée par la doc :
        la vider retirerait un des trois chemins de découverte."""
        task = ErrorContract()
        task.setup(tmp_path)
        _resoudre_error_contract(tmp_path)
        (tmp_path / "csv_reader.py").write_text("# vidé\n", encoding="utf-8")
        ok, detail = task.validate(tmp_path)
        assert ok is False
        assert "modifié" in detail, detail
