"""Tâches real_repo — projet documenté, 10+ fichiers, bruit réaliste.

Pourquoi ce palier existe : le bench est à 30/30, et `discovery` à 24/25 grâce
au garde (l'ouverture spontanée des docs reste à 0/5 sur `hidden_invariant`).
Les tâches discovery tournent dans un dossier de 3-5 fichiers. Un VRAI dépôt a
plus de bruit, plus de docs, et un signal-to-noise ratio plus bas.

Ce qui distingue ce palier de `discovery` :

| | discovery | real_repo |
|---|---|---|
| fichiers dans le dossier | 3-5 | 10-15 |
| docs | 1 | 3-4 (dont du bruit) |
| bruit (Makefile, pyproject…) | non | oui |
| la contrainte à trouver | dans UN doc ciblé | dans UN doc parmi plusieurs |

Cinq tâches, conçues pour ne PAS être à 5/5 au premier run :

| tâche | ce qui la rend discriminante |
|---|---|
| `batch_atomic_delete` | l'atomicité est dans INVARIANTS.md, le code local suffit |
| `null_byte_sanitize` | la politique sécurité est dans SECURITY.md, le code ne strip rien |
| `test_with_fixture` | la fixture est dans conftest, la convention dans TESTING.md |
| `fix_from_known_issue` | la cause est dans KNOWN_ISSUES.md, le test montre le symptôme |
| `create_submodule_readme` | le garde doc ne doit PAS bloquer une création de document |

Instrument à relever : taux d'ouverture spontanée de `docs/`, appels d'outils,
itérations, déclenchements du garde.

Contraintes : 100 % stdlib, fixture qui échoue par construction, chaque tâche a
ses deux tests (fixture rouge, référence verte) dans
`tests/test_bench_real_repo_tasks.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.framework import Task, register

_AVERTISSEMENT = (
    "⚠️ Ce projet a des conventions et invariants documentés dans `docs/`. "
    "Explore le dossier AVANT d'écrire (`list_files`, `read_file`, "
    "`search_in_files`) : une implémentation correcte mais ignorante des "
    "contraintes du projet sera rejetée."
)


def _project_skeleton(workdir: Path) -> None:
    """Fichiers de contexte communs — un vrai projet a du bruit."""
    (workdir / "docs").mkdir(parents=True, exist_ok=True)
    (workdir / "pyproject.toml").write_text(
        '[project]\nname = "myproject"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (workdir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\ndist/\n*.egg-info/\n",
        encoding="utf-8",
    )
    (workdir / "Makefile").write_text(
        ".PHONY: test lint\n\ntest:\n\tpytest -q\n\nlint:\n\truff check .\n",
        encoding="utf-8",
    )
    (workdir / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.1.0 (2026-08-01)\n\n- Initial release\n",
        encoding="utf-8",
    )


def _lire_json_final(sortie: str) -> list:
    """Dernière ligne de stdout → list. Les sondes impriment leur verdict en JSON."""
    return json.loads(sortie.strip().splitlines()[-1])


# ──────────────────────────────────────────────────────────────────────────── #
# 1. batch_atomic_delete — l'atomicité est dans les docs, pas le code        #
# ──────────────────────────────────────────────────────────────────────────── #


@register
class BatchAtomicDelete(Task):
    """Real repo : l'atomicité des opérations groupées est dans les docs.

    `RecordStore` a `add`, `get`, `delete` pour un seul enregistrement.
    L'énoncé demande `delete_many(ids)`. Le réflexe est de boucler sur
    `self.delete(id)` — simple, suit le code, et passe les tests VISIBLES.

    Mais `docs/INVARIANTS.md` dit : « toute opération groupée est atomique —
    si un id est inconnu, AUCUN n'est supprimé ». L'atomicité n'est visible
    nulle part dans le code existant (les opérations unitaires sont atomiques
    par nature). Seul le document la donne.

    Jumelle de `hidden_invariant` : le code local donne une histoire complète
    et cohérente. La contrainte vit dans un document qu'il faut ouvrir.

    Tests VISIBLES (test_store.py) :
      - `test_delete_many_basique` → passe avec un loop naïf ;
      - `test_delete_many_id_inconnu` → lève bien KeyError avec un loop naïf.
    Les deux sont VERTS avec l'implémentation fausse. Seule la sonde
    d'atomicité (validate) attrape la non-atomicité.
    """

    id = "real_repo/batch_atomic_delete"
    category = "real_repo"
    prompt = (
        "Ajoute à `RecordStore` (store.py) une méthode `delete_many(ids)` qui "
        "supprime plusieurs enregistrements en une fois. Elle reçoit une liste "
        "d'identifiants et ne renvoie rien. Si un identifiant est inconnu, elle "
        "lève `KeyError`.\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni docs/INVARIANTS.md ni test_store.py. "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        _project_skeleton(workdir)

        (workdir / "store.py").write_text(
            '"""Stockage d\'enregistrements en mémoire."""\n'
            "\n"
            "\n"
            "class RecordStore:\n"
            '    """Store indexé par identifiant entier auto-incrémenté."""\n'
            "\n"
            "    def __init__(self):\n"
            "        self._records = {}\n"
            "        self._next_id = 1\n"
            "\n"
            "    def add(self, data):\n"
            '        """Ajoute un enregistrement, renvoie son id."""\n'
            "        rid = self._next_id\n"
            "        self._next_id += 1\n"
            "        self._records[rid] = dict(data)\n"
            "        return rid\n"
            "\n"
            "    def get(self, rid):\n"
            '        """Renvoie une copie de l\'enregistrement."""\n'
            "        if rid not in self._records:\n"
            "            raise KeyError(f'enregistrement {rid} inconnu')\n"
            "        return dict(self._records[rid])\n"
            "\n"
            "    def delete(self, rid):\n"
            '        """Supprime un enregistrement."""\n'
            "        if rid not in self._records:\n"
            "            raise KeyError(f'enregistrement {rid} inconnu')\n"
            "        del self._records[rid]\n"
            "\n"
            "    def count(self):\n"
            "        return len(self._records)\n"
            "\n"
            "    def ids(self):\n"
            "        return sorted(self._records)\n",
            encoding="utf-8",
        )

        (workdir / "test_store.py").write_text(
            "import pytest\n"
            "from store import RecordStore\n"
            "\n"
            "\n"
            "def test_add_et_get():\n"
            "    s = RecordStore()\n"
            "    rid = s.add({'nom': 'alice'})\n"
            "    assert s.get(rid) == {'nom': 'alice'}\n"
            "\n"
            "\n"
            "def test_delete():\n"
            "    s = RecordStore()\n"
            "    rid = s.add({'nom': 'bob'})\n"
            "    s.delete(rid)\n"
            "    with pytest.raises(KeyError):\n"
            "        s.get(rid)\n"
            "\n"
            "\n"
            "def test_delete_many_basique():\n"
            "    s = RecordStore()\n"
            "    ids = [s.add({'n': i}) for i in range(5)]\n"
            "    s.delete_many(ids[:3])\n"
            "    assert s.count() == 2\n"
            "\n"
            "\n"
            "def test_delete_many_id_inconnu():\n"
            "    s = RecordStore()\n"
            "    s.add({'n': 1})\n"
            "    with pytest.raises(KeyError):\n"
            "        s.delete_many([1, 999])\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "INVARIANTS.md").write_text(
            "# Invariants du store\n"
            "\n"
            "Ces invariants ont été posés après des incidents de production.\n"
            "Ils s'appliquent à TOUTE nouvelle méthode, sans exception.\n"
            "\n"
            "## Identifiants\n"
            "\n"
            "Les identifiants sont des entiers auto-incrémentés, jamais\n"
            "réutilisés après suppression.\n"
            "\n"
            "## Copies défensives\n"
            "\n"
            "Les données renvoyées par `get` sont des copies. Modifier le dict\n"
            "retourné ne change pas le store. Idem pour `add` : le store\n"
            "conserve sa propre copie.\n"
            "\n"
            "## Atomicité des opérations groupées\n"
            "\n"
            "Toute opération qui touche **plusieurs** enregistrements est\n"
            "**atomique** : si l'un d'eux ne peut pas être traité (identifiant\n"
            "inconnu, validation échouée), **AUCUN** n'est modifié. L'appelant\n"
            "reçoit l'exception, et l'état du store est identique à ce qu'il\n"
            "était avant l'appel.\n"
            "\n"
            "Raison : un `delete_batch` partiel a supprimé les 3 premiers\n"
            "enregistrements d'une liste de 5 avant de lever sur le 4e.\n"
            "L'appelant a attrapé l'exception et a cru que rien n'avait\n"
            "changé — les 3 premiers étaient perdus.\n"
            "\n"
            "## Logging\n"
            "\n"
            "Toute opération d'écriture logue au niveau INFO l'identifiant et\n"
            "le type d'opération. Les lectures ne sont pas loguées.\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "API.md").write_text(
            "# API reference\n"
            "\n"
            "## RecordStore\n"
            "\n"
            "- `add(data) → int` : ajoute un enregistrement\n"
            "- `get(rid) → dict` : lit un enregistrement (copie)\n"
            "- `delete(rid) → None` : supprime un enregistrement\n"
            "- `count() → int` : nombre d'enregistrements\n"
            "- `ids() → list[int]` : identifiants triés\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "STYLE.md").write_text(
            "# Style du projet\n"
            "\n"
            "- Docstrings en français\n"
            "- Type hints encouragés\n"
            "- `ruff` comme linter\n",
            encoding="utf-8",
        )

        (workdir / "utils.py").write_text(
            '"""Utilitaires généraux."""\n'
            "\n"
            "\n"
            "def chunk(items, size):\n"
            "    for i in range(0, len(items), size):\n"
            "        yield items[i:i + size]\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        doc = workdir / "docs" / "INVARIANTS.md"
        if not doc.exists():
            return False, "docs/INVARIANTS.md supprimé (interdit)"
        if "atomique" not in doc.read_text(encoding="utf-8").lower():
            return False, "docs/INVARIANTS.md a été modifié (interdit)"

        test_path = workdir / "test_store.py"
        if not test_path.exists():
            return False, "test_store.py supprimé (interdit)"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        probe = (
            "import json\n"
            "from store import RecordStore\n"
            "manques = []\n"
            "\n"
            "# Happy path\n"
            "s = RecordStore()\n"
            "ids = [s.add({'n': i}) for i in range(5)]\n"
            "s.delete_many(ids[:3])\n"
            "if s.count() != 2:\n"
            "    manques.append('delete_many ne supprime pas correctement')\n"
            "\n"
            "# Atomicité : [valide, inconnu, valide] → AUCUN supprimé\n"
            "s2 = RecordStore()\n"
            "id1 = s2.add({'x': 1})\n"
            "id2 = s2.add({'x': 2})\n"
            "id3 = s2.add({'x': 3})\n"
            "try:\n"
            "    s2.delete_many([id1, 999, id3])\n"
            "except KeyError:\n"
            "    pass\n"
            "else:\n"
            "    manques.append('pas de KeyError sur id inconnu')\n"
            "if s2.count() != 3:\n"
            "    manques.append('atomicité violée : %d/3 restants' % s2.count())\n"
            "try:\n"
            "    s2.get(id1)\n"
            "except KeyError:\n"
            "    manques.append('id1 supprimé malgré erreur — NON atomique')\n"
            "try:\n"
            "    s2.get(id3)\n"
            "except KeyError:\n"
            "    manques.append('id3 supprimé malgré erreur — NON atomique')\n"
            "\n"
            "# Atomicité avec inconnu en tête : [inconnu, valide]\n"
            "s3 = RecordStore()\n"
            "idA = s3.add({'y': 1})\n"
            "try:\n"
            "    s3.delete_many([999, idA])\n"
            "except KeyError:\n"
            "    pass\n"
            "if s3.count() != 1:\n"
            "    manques.append('atomicité violée quand id inconnu est en tête')\n"
            "\n"
            "print(json.dumps(manques))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"sonde KO: {tail[-1][:90] if tail else 'no output'}"
        try:
            manques = _lire_json_final(pr.stdout)
        except Exception:
            return False, f"sortie sonde illisible: {pr.stdout.strip()[:80]}"
        if manques:
            return False, f"{len(manques)} invariant(s) violé(s) — {manques[0]}"
        return True, "atomicité tenue : [valide, inconnu] → aucun supprimé"


# ──────────────────────────────────────────────────────────────────────────── #
# 2. null_byte_sanitize — la politique sécurité est dans les docs            #
# ──────────────────────────────────────────────────────────────────────────── #


@register
class NullByteSanitize(Task):
    """Real repo : la politique de sécurité est dans les docs, pas le code.

    Le module `text_utils.py` a `clean(text)` (strip + collapse spaces) et
    `word_count(text)`. Aucun ne strip les null bytes — la politique de
    sécurité a été écrite APRÈS ces fonctions, et personne ne les a mises à
    jour (le doc le dit en toutes lettres).

    L'énoncé demande `normalize(text)` : lowercase + strip + collapse spaces.
    Le réflexe est de regarder `clean()`, d'y ajouter `.lower()`, et de
    rendre. `docs/SECURITY.md` dit : « toute nouvelle fonction de traitement
    de texte doit supprimer les octets nuls en PREMIÈRE opération ».

    Jumelle de `hidden_invariant` : le code local donne une histoire complète.
    La contrainte de sécurité n'est visible qu'en lisant un document.
    """

    id = "real_repo/null_byte_sanitize"
    category = "real_repo"
    prompt = (
        "Ajoute au module `text_utils.py` une fonction `normalize(text)` qui "
        "renvoie le texte en minuscules, détouré, et avec les espaces multiples "
        "réduits à un seul. Exemples :\n"
        "  - `normalize('  Hello   World  ')` → `'hello world'`\n"
        "  - `normalize('ABC')` → `'abc'`\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni docs/SECURITY.md ni test_text_utils.py."
    )

    def setup(self, workdir: Path) -> None:
        _project_skeleton(workdir)

        (workdir / "text_utils.py").write_text(
            '"""Fonctions utilitaires de traitement de texte."""\n'
            "\n"
            "import re\n"
            "\n"
            "\n"
            "def clean(text):\n"
            '    """Détourage + réduction des espaces multiples."""\n'
            "    return re.sub(r'\\s+', ' ', text.strip())\n"
            "\n"
            "\n"
            "def word_count(text):\n"
            '    """Nombre de mots non vides."""\n'
            "    return len(clean(text).split())\n"
            "\n"
            "\n"
            "def truncate(text, max_len=80):\n"
            '    """Tronque à max_len caractères, avec ellipse si tronqué."""\n'
            "    if len(text) <= max_len:\n"
            "        return text\n"
            "    return text[:max_len - 1] + '\\u2026'\n",
            encoding="utf-8",
        )

        (workdir / "test_text_utils.py").write_text(
            "from text_utils import clean, word_count, truncate\n"
            "\n"
            "\n"
            "def test_clean():\n"
            "    assert clean('  hello   world  ') == 'hello world'\n"
            "\n"
            "\n"
            "def test_word_count():\n"
            "    assert word_count('un deux trois') == 3\n"
            "\n"
            "\n"
            "def test_truncate():\n"
            "    assert truncate('abc', 5) == 'abc'\n"
            "    assert len(truncate('a' * 100, 80)) == 80\n"
            "\n"
            "\n"
            "def test_normalize_basique():\n"
            "    from text_utils import normalize\n"
            "    assert normalize('  Hello   World  ') == 'hello world'\n"
            "    assert normalize('ABC') == 'abc'\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "SECURITY.md").write_text(
            "# Politique de sécurité\n"
            "\n"
            "## Injection SQL\n"
            "\n"
            "Toutes les requêtes SQL utilisent des paramètres liés, jamais de\n"
            "concaténation de chaînes. Aucune exception.\n"
            "\n"
            "## Octets nuls dans les chaînes\n"
            "\n"
            "Un octet nul (`\\x00`) dans une chaîne de caractères provoque la\n"
            "troncature silencieuse quand la chaîne est passée à une fonction C\n"
            "(SQLite `LIKE`, `os.path.join`, regex compilée en C). Incident\n"
            "2026-01 : un nom de fichier contenant un null byte a corrompu\n"
            "l'index de recherche — seul le préfixe avant le `\\x00` était\n"
            "indexé, le reste était perdu.\n"
            "\n"
            "**Politique :** toute fonction qui reçoit du texte de l'extérieur\n"
            "doit **supprimer les octets nuls** (`text.replace('\\x00', '')`)\n"
            "en PREMIÈRE opération, avant tout autre traitement. S'applique à\n"
            "toute nouvelle fonction de traitement de texte.\n"
            "\n"
            "⚠️ Les fonctions existantes (`clean`, `word_count`, `truncate`)\n"
            "n'ont PAS été mises à jour. Ce n'est pas une raison pour ne pas\n"
            "appliquer la politique aux nouvelles fonctions.\n"
            "\n"
            "## Validation des chemins\n"
            "\n"
            "Tout chemin reçu en entrée est résolu (`Path.resolve()`) et vérifié\n"
            "dans les racines autorisées. Aucun path traversal.\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "ARCHITECTURE.md").write_text(
            "# Architecture\n"
            "\n"
            "## Modules\n"
            "\n"
            "- `text_utils.py` : fonctions de traitement de texte\n"
            "- `api.py` : endpoints HTTP\n"
            "\n"
            "## Conventions\n"
            "\n"
            "Un module = un domaine. Les imports circulaires sont interdits.\n",
            encoding="utf-8",
        )

        (workdir / "api.py").write_text(
            '"""Module API — placeholder."""\n', encoding="utf-8"
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        doc = workdir / "docs" / "SECURITY.md"
        if not doc.exists():
            return False, "docs/SECURITY.md supprimé (interdit)"
        if "octets nuls" not in doc.read_text(encoding="utf-8").lower():
            return False, "docs/SECURITY.md a été modifié (interdit)"

        test_path = workdir / "test_text_utils.py"
        if not test_path.exists():
            return False, "test_text_utils.py supprimé (interdit)"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        probe = (
            "import json\n"
            "from text_utils import normalize\n"
            "manques = []\n"
            "\n"
            "if normalize('  Hello   World  ') != 'hello world':\n"
            "    manques.append('basique : espaces + majuscules mal normalisés')\n"
            "if normalize('ABC') != 'abc':\n"
            "    manques.append('lowercase : ABC non converti')\n"
            "\n"
            "# Null bytes (politique SECURITY.md)\n"
            "r = normalize('hel\\x00lo wor\\x00ld')\n"
            "if '\\x00' in r:\n"
            "    manques.append('null bytes non supprimés (politique SECURITY.md)')\n"
            "if r != 'hello world':\n"
            "    manques.append('normalize + null bytes : %r attendu, %r obtenu'"
            " % ('hello world', r))\n"
            "\n"
            "if '\\x00' in normalize('\\x00abc\\x00'):\n"
            "    manques.append('null bytes non supprimés en tête/queue')\n"
            "\n"
            "print(json.dumps(manques))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"sonde KO: {tail[-1][:90] if tail else 'no output'}"
        try:
            manques = _lire_json_final(pr.stdout)
        except Exception:
            return False, f"sortie sonde illisible: {pr.stdout.strip()[:80]}"
        if manques:
            return False, f"{len(manques)} politique(s) ignorée(s) — {manques[0]}"
        return True, "null bytes supprimés + normalize correct"


# ──────────────────────────────────────────────────────────────────────────── #
# 3. test_with_fixture — la convention de test est dans les docs             #
# ──────────────────────────────────────────────────────────────────────────── #


@register
class TestWithFixture(Task):
    """Real repo : la convention de test exige la fixture de conftest.

    Le module `stats.py` a `average_score(records)` et
    `top_performers(records, threshold)`. L'énoncé demande d'ajouter des
    tests. Le dossier contient :

    - `conftest.py` avec une fixture `sample_records` (5 enregistrements
      aux propriétés statistiques connues) ;
    - `docs/TESTING.md` qui dit d'utiliser cette fixture ;
    - `tests/test_stats_existants.py` qui montre l'usage.

    Le conftest a un hook `pytest_collection_modifyitems` qui REJETTE les
    fonctions de test hors d'une classe `Test*`. C'est la convention de test
    du projet — un agent qui écrit `def test_average():` (le réflexe) voit
    l'erreur au pytest et doit la corriger.

    La fixture n'est pas FONCTIONNELLEMENT requise — l'agent peut créer ses
    propres données — mais la validation vérifie qu'elle est utilisée (la
    convention l'exige).
    """

    id = "real_repo/test_with_fixture"
    category = "real_repo"
    prompt = (
        "Ajoute des tests pour `average_score` et `top_performers` dans "
        "`stats.py`. Crée le fichier `test_stats.py`.\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni stats.py, ni conftest.py, ni docs/TESTING.md, ni "
        "tests/test_stats_existants.py."
    )

    def setup(self, workdir: Path) -> None:
        _project_skeleton(workdir)
        (workdir / "tests").mkdir(exist_ok=True)

        (workdir / "stats.py").write_text(
            '"""Fonctions statistiques sur des listes d\'enregistrements."""\n'
            "\n"
            "\n"
            "def average_score(records):\n"
            '    """Moyenne des scores des enregistrements actifs.\n'
            "\n"
            "    Seuls les enregistrements avec active=True comptent.\n"
            "    Renvoie 0.0 si aucun actif.\n"
            '    """\n'
            "    actifs = [r for r in records if r.get('active')]\n"
            "    if not actifs:\n"
            "        return 0.0\n"
            "    return round(sum(r['score'] for r in actifs) / len(actifs), 1)\n"
            "\n"
            "\n"
            "def top_performers(records, threshold=80):\n"
            '    """Enregistrements actifs au-dessus du seuil, triés par score décroissant."""\n'
            "    return sorted(\n"
            "        [r for r in records if r.get('active') and r['score'] >= threshold],\n"
            "        key=lambda r: r['score'],\n"
            "        reverse=True,\n"
            "    )\n",
            encoding="utf-8",
        )

        (workdir / "conftest.py").write_text(
            "import pytest\n"
            "\n"
            "\n"
            "@pytest.fixture\n"
            "def sample_records():\n"
            '    """Données de test standardisées — voir docs/TESTING.md.\n'
            "\n"
            "    Propriétés connues :\n"
            "      - 5 enregistrements, 3 actifs (alpha 85, beta 92, delta 78)\n"
            "      - average_score = (85 + 92 + 78) / 3 = 85.0\n"
            "      - top_performers(threshold=80) = [beta, alpha]\n"
            '    """\n'
            "    return [\n"
            "        {'id': 1, 'name': 'alpha', 'score': 85, 'active': True},\n"
            "        {'id': 2, 'name': 'beta', 'score': 92, 'active': True},\n"
            "        {'id': 3, 'name': 'gamma', 'score': 45, 'active': False},\n"
            "        {'id': 4, 'name': 'delta', 'score': 78, 'active': True},\n"
            "        {'id': 5, 'name': 'epsilon', 'score': 61, 'active': False},\n"
            "    ]\n"
            "\n"
            "\n"
            "def pytest_collection_modifyitems(config, items):\n"
            '    """Convention du projet : tous les tests sont dans des classes Test*."""\n'
            "    for item in items:\n"
            "        if not item.cls:\n"
            "            item.add_marker(pytest.mark.xfail(\n"
            "                reason='Convention : les tests doivent être dans une '\n"
            "                'classe Test*. Voir docs/TESTING.md.',\n"
            "                strict=True,\n"
            "            ))\n",
            encoding="utf-8",
        )

        (workdir / "tests" / "test_stats_existants.py").write_text(
            '"""Tests existants de stats.py — à ne PAS modifier."""\n'
            "\n"
            "from stats import average_score\n"
            "\n"
            "\n"
            "class TestAverageScoreBasique:\n"
            "    def test_liste_vide(self):\n"
            "        assert average_score([]) == 0.0\n"
            "\n"
            "    def test_aucun_actif(self):\n"
            "        recs = [{'score': 90, 'active': False}]\n"
            "        assert average_score(recs) == 0.0\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "TESTING.md").write_text(
            "# Conventions de test\n"
            "\n"
            "## Structure\n"
            "\n"
            "Tous les tests sont dans des **classes** nommées `Test<Sujet>`.\n"
            "Le conftest rejette les fonctions de test nues — c'est la convention\n"
            "du projet depuis la migration pytest 8.\n"
            "\n"
            "## Fixtures\n"
            "\n"
            "`conftest.py` fournit la fixture `sample_records` : 5\n"
            "enregistrements aux propriétés statistiques connues (3 actifs,\n"
            "average_score = 85.0). **Utiliser cette fixture** pour tous les\n"
            "tests de `stats.py` — ne pas créer ses propres données.\n"
            "\n"
            "## Nommage\n"
            "\n"
            "- Fichiers : `test_<module>.py`\n"
            "- Classes : `Test<Sujet>`\n"
            "- Méthodes : `test_<ce_qui_est_vérifié>`\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        for nom in ("conftest.py", "docs/TESTING.md",
                     "tests/test_stats_existants.py", "stats.py"):
            if not (workdir / nom).exists():
                return False, f"{nom} supprimé (interdit)"

        test_file = workdir / "test_stats.py"
        if not test_file.exists():
            return False, "test_stats.py non créé"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q", "--no-header",
             "-p", "no:cacheprovider", "--tb=short"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        src = test_file.read_text(encoding="utf-8")
        if "sample_records" not in src:
            return False, "fixture sample_records non utilisée (convention TESTING.md)"
        if "class Test" not in src:
            return False, "tests hors d'une classe Test* (convention TESTING.md)"

        if "passed" not in proc.stdout:
            return False, "aucun test passé"

        return True, "tests passent, fixture utilisée, classe Test*"


# ──────────────────────────────────────────────────────────────────────────── #
# 4. fix_from_known_issue — la cause est dans les docs                       #
# ──────────────────────────────────────────────────────────────────────────── #


@register
class FixFromKnownIssue(Task):
    """Real repo : le bug est documenté, le test montre le symptôme.

    `merge_configs(base, override)` fusionne deux dicts récursivement.
    `dict(base)` est une copie SUPERFICIELLE : les sous-dicts non surchargés
    sont PARTAGÉS entre base et résultat. Muter le résultat mute la base.

    Le test `test_merge_ne_mute_pas_base` échoue. L'agent peut le debugger
    depuis le test (le message d'assertion dit que base a changé), mais
    `docs/KNOWN_ISSUES.md` donne la cause exacte et le remède en une ligne.

    Instrument : l'agent ouvre-t-il KNOWN_ISSUES.md spontanément ?
    """

    id = "real_repo/fix_from_known_issue"
    category = "real_repo"
    prompt = (
        "Le test `test_merge_ne_mute_pas_base` dans `test_config.py` échoue. "
        "Corrige le bug dans `config_merger.py`.\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni test_config.py ni docs/KNOWN_ISSUES.md."
    )

    def setup(self, workdir: Path) -> None:
        _project_skeleton(workdir)

        (workdir / "config_merger.py").write_text(
            '"""Fusion récursive de configurations."""\n'
            "\n"
            "\n"
            "def merge_configs(base, override):\n"
            '    """Fusionne override dans base, renvoie un nouveau dict.\n'
            "\n"
            "    Les sous-dicts sont fusionnés récursivement.\n"
            '    """\n'
            "    result = dict(base)\n"
            "    for key, value in override.items():\n"
            "        if (\n"
            "            key in result\n"
            "            and isinstance(result[key], dict)\n"
            "            and isinstance(value, dict)\n"
            "        ):\n"
            "            result[key] = merge_configs(result[key], value)\n"
            "        else:\n"
            "            result[key] = value\n"
            "    return result\n",
            encoding="utf-8",
        )

        (workdir / "test_config.py").write_text(
            "from config_merger import merge_configs\n"
            "\n"
            "\n"
            "def test_merge_basique():\n"
            "    base = {'a': 1}\n"
            "    result = merge_configs(base, {'b': 2})\n"
            "    assert result == {'a': 1, 'b': 2}\n"
            "\n"
            "\n"
            "def test_merge_recursif():\n"
            "    base = {'db': {'host': 'localhost', 'port': 5432}}\n"
            "    result = merge_configs(base, {'db': {'port': 5433}})\n"
            "    assert result == {'db': {'host': 'localhost', 'port': 5433}}\n"
            "\n"
            "\n"
            "def test_merge_ne_mute_pas_base():\n"
            "    base = {'db': {'host': 'localhost'}, 'cache': {'ttl': 300}}\n"
            "    result = merge_configs(base, {'db': {'port': 5432}})\n"
            "    result['cache']['ttl'] = 600\n"
            "    assert base['cache']['ttl'] == 300, (\n"
            "        'merge_configs a partagé le sous-dict cache entre base et result'\n"
            "    )\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "KNOWN_ISSUES.md").write_text(
            "# Problèmes connus\n"
            "\n"
            "## config_merger.py — copie superficielle contamine la base\n"
            "\n"
            "`merge_configs()` utilise `dict(base)` qui est une copie\n"
            "**superficielle**. Les sous-dicts qui ne sont PAS présents dans\n"
            "`override` sont partagés entre `base` et le résultat. Muter le\n"
            "résultat mute la base.\n"
            "\n"
            "Exemple : si `base = {'db': {...}, 'cache': {'ttl': 300}}` et\n"
            "qu'on fusionne avec `{'db': {'port': 5432}}`, le sous-dict\n"
            "`cache` est partagé. `result['cache']['ttl'] = 600` modifie\n"
            "aussi `base['cache']['ttl']`.\n"
            "\n"
            "**Remède :** remplacer `dict(base)` par `copy.deepcopy(base)`.\n"
            "\n"
            "## api.py — timeout non configurable\n"
            "\n"
            "Le timeout HTTP est fixé à 30 s en dur. Devrait lire\n"
            "`APP_TIMEOUT` de l'environnement.\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "ARCHITECTURE.md").write_text(
            "# Architecture\n"
            "\n"
            "- `config_merger.py` : fusion récursive de configurations\n"
            "- `api.py` : endpoints HTTP\n"
            "- `store.py` : stockage en mémoire\n",
            encoding="utf-8",
        )

        (workdir / "api.py").write_text(
            '"""Module API — placeholder."""\n', encoding="utf-8"
        )
        (workdir / "store.py").write_text(
            '"""Store — placeholder."""\n', encoding="utf-8"
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        for nom in ("test_config.py", "docs/KNOWN_ISSUES.md"):
            if not (workdir / nom).exists():
                return False, f"{nom} supprimé (interdit)"
        if "copie superficielle" not in (workdir / "docs" / "KNOWN_ISSUES.md").read_text(
            encoding="utf-8"
        ):
            return False, "docs/KNOWN_ISSUES.md a été modifié (interdit)"
        if "test_merge_ne_mute_pas_base" not in (workdir / "test_config.py").read_text(
            encoding="utf-8"
        ):
            return False, "test_config.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             str(workdir / "test_config.py"), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"
        return True, "3/3 tests passent — copie profonde corrigée"


# ──────────────────────────────────────────────────────────────────────────── #
# 5. create_submodule_readme — création de doc, le garde ne doit PAS bloquer #
# ──────────────────────────────────────────────────────────────────────────── #


@register
class CreateSubmoduleReadme(Task):
    """Real repo : créer un README n'est PAS « écrire sans lire les docs ».

    Le garde « décisions jamais ouvertes » (CLAUDE.md) refuse une conclusion
    posée après écriture quand l'agent n'a ouvert aucun document. Mais un
    README PRODUIT par l'agent est exclu de l'inventaire — c'est le test
    du faux positif, déjà attrapé sur les scénarios de rejeu 04 et 12.

    Le dossier `utils/` contient deux modules Python sans README. L'agent
    doit créer `utils/README.md` qui documente les fonctions publiques.
    Le garde peut se déclencher (il y a des docs dans `docs/` que l'agent
    n'aura pas lues) mais ne doit pas EMPÊCHER la création du README.

    La validation vérifie seulement que le README existe et mentionne les
    modules. L'intérêt de cette tâche est l'instrumentation : itérations
    et déclenchements du garde.
    """

    id = "real_repo/create_submodule_readme"
    category = "real_repo"
    prompt = (
        "Crée un fichier `utils/README.md` qui documente le sous-module "
        "`utils/`. Le README doit lister les modules présents et décrire "
        "leurs fonctions publiques.\n"
        "Ne modifie aucun fichier existant."
    )

    def setup(self, workdir: Path) -> None:
        _project_skeleton(workdir)
        (workdir / "utils").mkdir(exist_ok=True)

        (workdir / "utils" / "__init__.py").write_text(
            '"""Sous-module utilitaires."""\n', encoding="utf-8"
        )

        (workdir / "utils" / "helpers.py").write_text(
            '"""Fonctions utilitaires générales."""\n'
            "\n"
            "\n"
            "def flatten(nested):\n"
            '    """Aplatit une liste imbriquée récursivement."""\n'
            "    out = []\n"
            "    for item in nested:\n"
            "        if isinstance(item, list):\n"
            "            out.extend(flatten(item))\n"
            "        else:\n"
            "            out.append(item)\n"
            "    return out\n"
            "\n"
            "\n"
            "def chunk(items, size):\n"
            '    """Découpe en sous-listes de taille donnée."""\n'
            "    for i in range(0, len(items), size):\n"
            "        yield items[i:i + size]\n"
            "\n"
            "\n"
            "def first(iterable, default=None):\n"
            '    """Premier élément, ou default."""\n'
            "    return next(iter(iterable), default)\n",
            encoding="utf-8",
        )

        (workdir / "utils" / "formatters.py").write_text(
            '"""Fonctions de formatage de texte."""\n'
            "\n"
            "\n"
            "def table(rows, headers):\n"
            '    """Formate des lignes en table ASCII alignée."""\n'
            "    if not rows:\n"
            "        return ''\n"
            "    widths = [\n"
            "        max(len(str(h)), max(len(str(r[i])) for r in rows))\n"
            "        for i, h in enumerate(headers)\n"
            "    ]\n"
            "    sep = '+'.join('-' * (w + 2) for w in widths)\n"
            "    def fmt(vals):\n"
            "        return '|'.join(f' {str(v):<{w}} ' for v, w in zip(vals, widths))\n"
            "    lines = [fmt(headers), sep]\n"
            "    lines.extend(fmt(r) for r in rows)\n"
            "    return '\\n'.join(lines)\n"
            "\n"
            "\n"
            "def indent(text, prefix='  '):\n"
            '    """Indente chaque ligne d\'un texte."""\n'
            "    return '\\n'.join(prefix + line for line in text.splitlines())\n",
            encoding="utf-8",
        )

        (workdir / "docs" / "ARCHITECTURE.md").write_text(
            "# Architecture\n"
            "\n"
            "## Modules principaux\n"
            "\n"
            "- `main.py` : point d'entrée\n"
            "- `utils/` : utilitaires réutilisables (helpers, formatters)\n"
            "\n"
            "## Conventions\n"
            "\n"
            "Chaque sous-module a son propre README.md.\n",
            encoding="utf-8",
        )

        (workdir / "main.py").write_text(
            '"""Point d\'entrée principal."""\n'
            "\n"
            "from utils.helpers import flatten\n"
            "from utils.formatters import table\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        readme = workdir / "utils" / "README.md"
        if not readme.exists():
            return False, "utils/README.md non créé"

        contenu = readme.read_text(encoding="utf-8").lower()
        if len(contenu) < 100:
            return False, f"README trop court ({len(contenu)} caractères)"

        modules_trouves = []
        for nom in ("helpers", "formatters"):
            if nom in contenu:
                modules_trouves.append(nom)

        if len(modules_trouves) < 2:
            return False, (
                f"README ne mentionne que {modules_trouves or 'aucun module'} "
                f"— attendu : helpers et formatters"
            )

        for fichier in ("utils/helpers.py", "utils/formatters.py",
                         "docs/ARCHITECTURE.md", "main.py"):
            if not (workdir / fichier).exists():
                return False, f"{fichier} supprimé (interdit)"

        return True, "README créé, mentionne helpers et formatters"
