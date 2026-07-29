"""Tâches discovery — la contrainte décisive n'est PAS dans l'énoncé.

Pourquoi ce palier existe, et pourquoi il ne s'appelle pas « au-dessus d'expert » :
le palier `expert` a été construit en rendant les tâches PLUS DURES, et il passe
5/5 (#174). Empiler de la difficulté sur le même axe ne rouvre pas d'écart.
Celui-ci change d'axe. Ce n'est pas un cran de plus sur une échelle, c'est autre
chose qu'on mesure — d'où un nom qui décrit le mécanisme, pas un rang.

Ce qui est mesuré : **est-ce que l'agent va chercher ce qu'on ne lui a pas dit ?**
Dans chaque tâche l'énoncé donne l'objectif et rien d'autre. Ce qui décide du
résultat vit ailleurs dans le dossier :

| tâche | où dort la contrainte | ce que fait l'implémentation évidente |
|---|---|---|
| `hidden_invariant` | un journal d'incidents | réintroduit un bug déjà corrigé une fois |
| `config_precedence` | le README | invente un ordre de priorité plausible et faux |
| `error_contract` | un module voisin + CONVENTIONS.md | lève `ValueError` |
| `data_contract` | les données elles-mêmes | plante sur le premier montant textuel |

Ce n'est PAS un piège, et la distinction compte : chaque énoncé annonce en toutes
lettres que le projet impose des contraintes absentes de l'énoncé, et l'agent
dispose de `list_files`, `read_file` et `search_in_files`. Échouer ici, c'est ne
pas avoir regardé — pas avoir manqué d'information. Une tâche dont la réponse
n'est pas trouvable mesurerait la chance, et un test le vérifierait mal.

Contraintes d'écriture identiques aux autres paliers : 100 % stdlib, fixture qui
échoue par construction, gardes contre le codage en dur, et la documentation que
l'agent doit lire est protégée contre la réécriture — déplacer la cible n'est pas
l'atteindre.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from bench.framework import Task, register

# Rappel commun aux quatre énoncés. Sans lui, la tâche mesurerait la divination :
# un agent qui implémente proprement ce qu'on lui demande ne serait pas fautif.
_AVERTISSEMENT = (
    "⚠️ Ce projet impose des contraintes qui NE SONT PAS écrites dans cet énoncé. "
    "Elles sont dans le dossier de travail — code existant, documentation, données. "
    "Explore AVANT d'écrire (`list_files`, `read_file`, `search_in_files`) : une "
    "implémentation raisonnable mais ignorante des contraintes du projet sera "
    "rejetée."
)


def _lire_json_final(sortie: str) -> dict:
    """Dernière ligne de stdout → dict. Les sondes impriment leur verdict en JSON ;
    tout ce que le code de l'agent a pu écrire avant est ignoré."""
    return json.loads(sortie.strip().splitlines()[-1])


def _sonde_invariants(module: str, classe: str, ref_copie: str, ref_cle: str) -> str:
    """Sonde PARTAGÉE par `hidden_invariant` et son témoin `first_write_method`.

    Les deux tâches forment une expérience contrôlée : seule la présence d'un
    modèle local à imiter les distingue. Deux copies de cette sonde finiraient
    par diverger d'un détail, et la comparaison ne voudrait plus rien dire —
    on croirait mesurer le contexte alors qu'on mesurerait l'écart des sondes.
    Un test verrouille le fait que les deux passent bien par ici.

    ⚠️ La valeur de contrôle est imbriquée sur DEUX niveaux (`{'liste': [7]}`).
    Avec une seule liste d'entiers, `copy.copy` suffisait et passait la mesure :
    la décision écrite parle d'« une liste imbriquée DANS la valeur », seule
    forme où superficiel et profond diffèrent. Constaté en écrivant les tests —
    la sonde validait `copy.copy`.
    """
    return (
        "import json\n"
        f"from {module} import {classe}\n"
        "manques = []\n"
        f"c = {classe}()\n"
        "source = {'a': 1, 'b': [1, 2], 'c': {'liste': [7]}}\n"
        "c.set_many(source)\n"
        "if c.get('a') != 1 or c.get('b') != [1, 2] or c.get('c') != {'liste': [7]}:\n"
        "    manques.append('set_many ne range pas les valeurs')\n"
        "source['a'] = 999\n"
        "if c.get('a') != 1:\n"
        f"    manques.append('le dict de l_appelant est conservé ({ref_copie})')\n"
        "source['b'].append(3)\n"
        "if c.get('b') != [1, 2]:\n"
        f"    manques.append('valeur non copiée : la liste est partagée ({ref_copie})')\n"
        "source['c']['liste'].append(9)\n"
        "if c.get('c') != {'liste': [7]}:\n"
        "    manques.append('copie superficielle : la liste imbriquée est partagée')\n"
        "try:\n"
        f"    {classe}().set_many({{7: 'x'}})\n"
        "except TypeError:\n"
        "    pass\n"
        "except Exception as exc:\n"
        "    manques.append('clé non str : %s levée, TypeError attendue'"
        " % type(exc).__name__)\n"
        "else:\n"
        f"    manques.append('clé non str acceptée ({ref_cle})')\n"
        "print(json.dumps(manques))\n"
    )


@register
class HiddenInvariant(Task):
    """Discovery : un incident passé interdit l'implémentation évidente.

    `set_many(mapping)` s'écrit en trois lignes — et ces trois lignes conservent
    l'objet de l'appelant, donc le cache change dans son dos dès que l'appelant
    réutilise son dict. Ce bug a déjà été vécu : il est consigné dans
    `docs/INCIDENTS.md`, avec deux autres incidents sans rapport pour que le
    fichier soit à lire, pas à survoler.

    Deux contraintes à trouver, de difficulté volontairement inégale :
      - **clés `str` uniquement** — visible AUSSI dans `Cache.set`, donc
        atteignable en lisant le code qu'on prolonge ;
      - **copie profonde des valeurs** — nulle part dans le code, puisque `set`
        n'accepte que des scalaires : rien à copier, donc rien à imiter. Seul le
        journal d'incidents la donne.

    C'est cette seconde qui départage : elle n'est accessible que par la lecture
    d'un fichier que l'énoncé ne nomme pas.
    """

    id = "discovery/hidden_invariant"
    category = "discovery"
    prompt = (
        "Ajoute à la classe `Cache` de cache.py une méthode `set_many(mapping)` "
        "qui enregistre en une fois toutes les paires clé/valeur d'un "
        "dictionnaire. Elle ne renvoie rien.\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni docs/INCIDENTS.md ni test_cache.py. "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "docs").mkdir(parents=True, exist_ok=True)
        (workdir / "cache.py").write_text(
            "class Cache:\n"
            '    """Cache mémoire du projet."""\n'
            "\n"
            "    def __init__(self):\n"
            "        self._data = {}\n"
            "\n"
            "    def set(self, key, value):\n"
            "        if not isinstance(key, str):\n"
            "            raise TypeError('clé non str: %r' % (key,))\n"
            "        self._data[key] = value\n"
            "\n"
            "    def get(self, key, default=None):\n"
            "        return self._data.get(key, default)\n"
            "\n"
            "    def keys(self):\n"
            "        return sorted(self._data)\n",
            encoding="utf-8",
        )
        (workdir / "docs" / "INCIDENTS.md").write_text(
            "# Journal des incidents\n"
            "\n"
            "Les décisions prises ici sont contraignantes pour tout code ajouté au\n"
            "projet, y compris les nouvelles API. Ne pas les redécouvrir en\n"
            "production une deuxième fois.\n"
            "\n"
            "## 2026-01 — Journalisation qui bloquait la boucle d'événements\n"
            "\n"
            "Le handler de log écrivait sur disque de façon synchrone depuis une\n"
            "coroutine. Sous charge, la latence p99 est passée de 40 ms à 3 s.\n"
            "**Décision :** aucune écriture disque synchrone dans du code async.\n"
            "\n"
            "## 2026-03 — Le cache mutait les objets de l'appelant\n"
            "\n"
            "Une API `set_dict(d)` avait été ajoutée au cache. Elle rangeait le\n"
            "dictionnaire reçu tel quel. Un appelant réutilisait son dict comme\n"
            "tampon entre deux requêtes : le contenu du cache changeait donc dans\n"
            "son dos, sans qu'aucun appel au cache ait eu lieu. Trois jours de\n"
            "recherche pour un bug qui ne ressemblait pas à un bug de cache.\n"
            "\n"
            "**Décision :** le cache ne CONSERVE JAMAIS un objet fourni par\n"
            "l'appelant. Il en stocke une **copie profonde** (`copy.deepcopy`) —\n"
            "profonde et non superficielle, car le cas réel était une liste\n"
            "imbriquée dans la valeur, que la copie superficielle partageait\n"
            "encore. S'applique aux valeurs de toute API d'écriture, présente ou\n"
            "future.\n"
            "\n"
            "## 2026-05 — Collision de clés entre `1` et `'1'`\n"
            "\n"
            "Des clés entières et textuelles coexistaient et s'écrasaient selon\n"
            "l'ordre d'insertion. **Décision :** les clés du cache sont des `str`,\n"
            "et toute autre valeur lève `TypeError`. Vaut aussi pour les API qui\n"
            "écrivent plusieurs clés d'un coup.\n",
            encoding="utf-8",
        )
        (workdir / "test_cache.py").write_text(
            "import pytest\n"
            "\n"
            "from cache import Cache\n"
            "\n"
            "\n"
            "def test_set_get():\n"
            "    c = Cache()\n"
            "    c.set('a', 1)\n"
            "    assert c.get('a') == 1\n"
            "\n"
            "\n"
            "def test_cle_non_str_refusee():\n"
            "    with pytest.raises(TypeError):\n"
            "        Cache().set(7, 'x')\n"
            "\n"
            "\n"
            "def test_set_many_enregistre_tout():\n"
            "    c = Cache()\n"
            "    c.set_many({'a': 1, 'b': 2})\n"
            "    assert c.keys() == ['a', 'b']\n"
            "    assert c.get('b') == 2\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        doc = workdir / "docs" / "INCIDENTS.md"
        if not doc.exists():
            return False, "docs/INCIDENTS.md supprimé (interdit)"
        doc_src = doc.read_text(encoding="utf-8")
        for marque in ("copie profonde", "les clés du cache sont des `str`"):
            if marque not in doc_src:
                return False, "docs/INCIDENTS.md a été modifié (interdit)"

        test_path = workdir / "test_cache.py"
        if not test_path.exists():
            return False, "test_cache.py supprimé (interdit)"
        if "test_set_many_enregistre_tout" not in test_path.read_text(encoding="utf-8"):
            return False, "test_cache.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        # Les deux invariants du journal, éprouvés séparément pour que le message
        # dise LEQUEL manque — « set_many incorrect » n'aiderait personne.
        probe = _sonde_invariants("cache", "Cache", "incident 2026-03", "incident 2026-05")
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
            return False, f"{len(manques)} invariant(s) non tenu(s) — {manques[0]}"
        return True, "copie profonde + clés str : les deux décisions du journal tenues"


@register
class ConfigPrecedence(Task):
    """Discovery : l'ordre de priorité est dans le README, pas dans l'énoncé.

    « Ajoute la variable d'environnement et l'option CLI » se code sans réfléchir,
    et produit un ordre de priorité inventé. Le README en fixe un, plus trois
    règles de bord qu'aucune intuition ne donne :

      - une variable d'environnement **vide** est ignorée (et non lue comme 0) ;
      - `--timeout 0` est une consigne explicite et vaut 0 ;
      - une valeur non entière lève `ValueError` en nommant sa provenance.

    La deuxième et la troisième se contredisent en apparence — vide ≠ absent ≠ 0 —
    ce qui rend l'invention presque sûre d'échouer.
    """

    id = "discovery/config_precedence"
    category = "discovery"
    prompt = (
        "Le fichier settings.py expose `load_settings(argv=None)`, qui construit la "
        "configuration effective. Aujourd'hui il ne connaît que la valeur par défaut "
        "et le fichier settings.json. Ajoute la prise en charge de la variable "
        "d'environnement `APP_TIMEOUT` et de l'option de ligne de commande "
        "`--timeout` (lue dans `argv`).\n"
        + _AVERTISSEMENT
        + "\nNe modifie pas README.md. `load_settings` doit continuer à renvoyer un "
        "dict contenant la clé `timeout`."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "settings.py").write_text(
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "DEFAULT_TIMEOUT = 30\n"
            "CONFIG_PATH = Path(__file__).with_name('settings.json')\n"
            "\n"
            "\n"
            "def load_settings(argv=None):\n"
            '    """Configuration effective du service."""\n'
            "    valeurs = {'timeout': DEFAULT_TIMEOUT}\n"
            "    if CONFIG_PATH.exists():\n"
            "        fichier = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
            "        if 'timeout' in fichier:\n"
            "            valeurs['timeout'] = fichier['timeout']\n"
            "    # TODO: APP_TIMEOUT et --timeout\n"
            "    return valeurs\n",
            encoding="utf-8",
        )
        (workdir / "settings.json").write_text('{"timeout": 45}\n', encoding="utf-8")
        (workdir / "README.md").write_text(
            "# Service\n"
            "\n"
            "## Configuration\n"
            "\n"
            "Le réglage `timeout` (en secondes) peut venir de quatre endroits. Ils\n"
            "sont classés du plus fort au plus faible — **le premier présent gagne**,\n"
            "les suivants sont ignorés :\n"
            "\n"
            "1. l'option de ligne de commande `--timeout N` ;\n"
            "2. la variable d'environnement `APP_TIMEOUT` ;\n"
            "3. la clé `timeout` de `settings.json` ;\n"
            "4. à défaut, `DEFAULT_TIMEOUT` (30).\n"
            "\n"
            "### Règles de bord\n"
            "\n"
            "Ces trois points ont été fixés après des incidents d'exploitation, et\n"
            "ne sont pas négociables :\n"
            "\n"
            "- **`APP_TIMEOUT` vide vaut ABSENTE.** Les orchestrateurs exportent\n"
            "  volontiers `APP_TIMEOUT=` quand aucune valeur n'est configurée. La\n"
            "  lire comme `0` mettait le service en timeout immédiat au démarrage.\n"
            "  Une chaîne vide (ou uniquement des espaces) est donc ignorée, et on\n"
            "  passe au niveau suivant.\n"
            "- **`--timeout 0` vaut ZÉRO.** À l'inverse du cas ci-dessus, un zéro\n"
            "  écrit explicitement sur la ligne de commande est une consigne : il\n"
            "  signifie « pas de timeout » et doit être respecté tel quel. Ne pas\n"
            "  le confondre avec une absence de valeur (piège classique du test de\n"
            "  vérité sur un entier).\n"
            "- **Une valeur non entière est une erreur, pas un repli.** Une valeur\n"
            "  illisible lève `ValueError`, et le message doit nommer sa provenance\n"
            "  (`APP_TIMEOUT` ou `--timeout`) : un repli silencieux sur le défaut a\n"
            "  déjà masqué une faute de frappe en production pendant six semaines.\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import os
        import subprocess
        import sys

        readme = workdir / "README.md"
        if not readme.exists():
            return False, "README.md supprimé (interdit)"
        readme_src = readme.read_text(encoding="utf-8")
        for marque in ("`APP_TIMEOUT` vide vaut ABSENTE", "`--timeout 0` vaut ZÉRO"):
            if marque not in readme_src:
                return False, "README.md a été modifié (interdit)"

        src_path = workdir / "settings.py"
        if not src_path.exists():
            return False, "settings.py absent"

        # Un sous-processus par cas : l'environnement est posé DE L'EXTÉRIEUR.
        # Muter os.environ dans une seule sonde supposerait que load_settings relit
        # l'env à chaque appel — vrai pour une implémentation naturelle, mais une
        # lecture au moment de l'import serait alors punie à tort.
        cas = [
            # (libellé, fichier json, APP_TIMEOUT, argv, attendu | 'ValueError')
            ("défaut seul", None, None, [], 30),
            ("fichier", {"timeout": 45}, None, [], 45),
            ("env > fichier", {"timeout": 45}, "60", [], 60),
            ("cli > env", {"timeout": 45}, "60", ["--timeout", "90"], 90),
            ("env vide ignorée", {"timeout": 45}, "", [], 45),
            ("env espaces ignorée", {"timeout": 45}, "   ", [], 45),
            ("env vide sans fichier", None, "", [], 30),
            ("cli zéro explicite", {"timeout": 45}, "60", ["--timeout", "0"], 0),
            ("env illisible", {"timeout": 45}, "abc", [], "ValueError"),
            ("cli illisible", None, None, ["--timeout", "x"], "ValueError"),
        ]

        echecs: list[str] = []
        for libelle, fichier, env_val, argv, attendu in cas:
            cfg = workdir / "settings.json"
            if fichier is None:
                cfg.unlink(missing_ok=True)
            else:
                cfg.write_text(json.dumps(fichier), encoding="utf-8")

            env = {k: v for k, v in os.environ.items() if k != "APP_TIMEOUT"}
            if env_val is not None:
                env["APP_TIMEOUT"] = env_val

            snippet = (
                "import json, sys\n"
                "from settings import load_settings\n"
                "try:\n"
                f"    v = load_settings({argv!r})['timeout']\n"
                "except ValueError as exc:\n"
                "    print(json.dumps({'ValueError': str(exc)})); sys.exit(0)\n"
                "except Exception as exc:\n"
                "    print(json.dumps({'autre': type(exc).__name__})); sys.exit(0)\n"
                "print(json.dumps({'valeur': v}))\n"
            )
            pr = subprocess.run(
                [sys.executable, "-c", snippet], capture_output=True, text=True,
                timeout=60, cwd=workdir, env=env,
            )
            if pr.returncode != 0:
                tail = (pr.stdout + pr.stderr).strip().splitlines()
                echecs.append(f"{libelle}: sonde KO ({tail[-1][:50] if tail else '?'})")
                continue
            try:
                d = _lire_json_final(pr.stdout)
            except Exception:
                echecs.append(f"{libelle}: sortie illisible")
                continue

            if attendu == "ValueError":
                if "ValueError" not in d:
                    echecs.append(f"{libelle}: ValueError attendue, obtenu {d}")
                elif not any(
                    n.lower() in d["ValueError"].lower() for n in ("APP_TIMEOUT", "timeout")
                ):
                    echecs.append(f"{libelle}: ValueError sans provenance nommée")
            elif d.get("valeur") != attendu:
                echecs.append(f"{libelle}: {attendu} attendu, obtenu {d}")

        # L'état de départ est rendu : une session laissée sans settings.json
        # ferait échouer un diagnostic manuel après coup.
        (workdir / "settings.json").write_text('{"timeout": 45}\n', encoding="utf-8")

        if echecs:
            return False, f"{len(echecs)}/{len(cas)} cas hors README — {echecs[0]}"
        return True, f"{len(cas)}/{len(cas)} cas : priorité et règles de bord respectées"


@register
class ErrorContract(Task):
    """Discovery : la convention d'erreurs du projet, jamais énoncée.

    Écrire un parseur qui lève `ValueError` sur une entrée malformée est le bon
    réflexe Python — et c'est faux ici. Le projet a une hiérarchie d'exceptions
    (`errors.py`), une convention écrite (`docs/CONVENTIONS.md`), et un module
    voisin déjà conforme (`csv_reader.py`) qui montre l'usage.

    Trois chemins de découverte pour un même fait, ce qui rend l'échec
    difficile à mettre sur le compte d'une information introuvable : il suffit
    d'ouvrir l'un des trois. Le numéro de ligne exigé dans l'exception est la
    partie qui ne s'invente pas — elle est visible dans le voisin.
    """

    id = "discovery/error_contract"
    category = "discovery"
    prompt = (
        "Crée le fichier ini_reader.py avec une fonction `parse_ini(texte)` qui "
        "transforme un texte de type INI en dictionnaire de dictionnaires.\n"
        "Format : une section s'écrit `[nom]` sur sa propre ligne ; une entrée "
        "s'écrit `cle = valeur` ; les lignes vides et celles commençant par `#` "
        "sont ignorées. Les clés et valeurs sont détourées de leurs espaces. "
        "Exemple : `[a]\\nx = 1` donne `{'a': {'x': '1'}}` (les valeurs restent "
        "des chaînes).\n"
        "Sont malformées : une entrée avant toute section, une ligne sans `=` qui "
        "n'est ni une section ni un commentaire, et une section au nom vide "
        "(`[]`).\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni errors.py, ni csv_reader.py, ni docs/CONVENTIONS.md."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "docs").mkdir(parents=True, exist_ok=True)
        (workdir / "errors.py").write_text(
            '"""Hiérarchie d\'exceptions du projet."""\n'
            "\n"
            "\n"
            "class AppError(Exception):\n"
            '    """Racine de toutes les erreurs du projet."""\n'
            "\n"
            "\n"
            "class ParseError(AppError):\n"
            '    """Entrée malformée. `line` est le numéro de ligne (1-indexé)."""\n'
            "\n"
            "    def __init__(self, message, *, line):\n"
            "        super().__init__(message)\n"
            "        self.line = line\n"
            "\n"
            "\n"
            "class ConfigError(AppError):\n"
            '    """Configuration incohérente."""\n',
            encoding="utf-8",
        )
        (workdir / "csv_reader.py").write_text(
            '"""Lecteur CSV maison — conforme à docs/CONVENTIONS.md."""\n'
            "\n"
            "from errors import ParseError\n"
            "\n"
            "\n"
            "def parse_csv(texte, colonnes):\n"
            "    lignes = [x for x in texte.splitlines() if x.strip()]\n"
            "    sortie = []\n"
            "    for numero, ligne in enumerate(lignes, start=1):\n"
            "        champs = ligne.split(',')\n"
            "        if len(champs) != colonnes:\n"
            "            # Le numéro de ligne est 1-indexé et compte la ligne FAUTIVE.\n"
            "            raise ParseError(\n"
            "                '%d champs attendus, %d trouvés' % (colonnes, len(champs)),\n"
            "                line=numero,\n"
            "            )\n"
            "        sortie.append([c.strip() for c in champs])\n"
            "    return sortie\n",
            encoding="utf-8",
        )
        (workdir / "docs" / "CONVENTIONS.md").write_text(
            "# Conventions du projet\n"
            "\n"
            "## Nommage\n"
            "\n"
            "Modules en `snake_case`. Un lecteur de format s'appelle "
            "`<format>_reader.py`\n"
            "et expose `parse_<format>`.\n"
            "\n"
            "## Erreurs\n"
            "\n"
            "**Aucune exception native ne franchit une frontière de module.** Un\n"
            "appelant ne doit jamais avoir à attraper `ValueError`, `KeyError` ou\n"
            "`TypeError` venant de notre code : il attrape `AppError` ou une de ses\n"
            "sous-classes, définies dans `errors.py`. C'est ce qui permet au\n"
            "gestionnaire de niveau supérieur de distinguer nos erreurs de celles\n"
            "des bibliothèques tierces.\n"
            "\n"
            "En particulier :\n"
            "\n"
            "- toute entrée malformée lève **`ParseError`** ;\n"
            "- `ParseError` porte OBLIGATOIREMENT le numéro de la ligne fautive,\n"
            "  passé en argument nommé `line=`, **1-indexé** (la première ligne du\n"
            "  texte est la ligne 1). Sans lui, l'utilisateur reçoit « fichier\n"
            "  invalide » sans savoir où regarder — c'est la plainte n°1 de\n"
            "  l'assistance ;\n"
            "- le message reste court et factuel, sans le contenu de la ligne (il\n"
            "  peut porter des données personnelles).\n"
            "\n"
            "`csv_reader.py` est l'implémentation de référence : s'en inspirer pour\n"
            "tout nouveau lecteur.\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        for nom, marques in (
            ("errors.py", ("class ParseError(AppError)",)),
            ("csv_reader.py", ("raise ParseError(",)),
            ("docs/CONVENTIONS.md", ("lève **`ParseError`**", "1-indexé")),
        ):
            chemin = workdir / nom
            if not chemin.exists():
                return False, f"{nom} supprimé (interdit)"
            contenu = chemin.read_text(encoding="utf-8")
            for marque in marques:
                if marque not in contenu:
                    return False, f"{nom} a été modifié (interdit)"

        if not (workdir / "ini_reader.py").exists():
            return False, "ini_reader.py non créé"

        # `line` attendu : 1-indexé sur le texte COMPLET, lignes vides et
        # commentaires compris — c'est ce que « la première ligne du texte est la
        # ligne 1 » impose, et c'est ce que fait le voisin de référence.
        probe = (
            "import json\n"
            "from errors import AppError, ParseError\n"
            "from ini_reader import parse_ini\n"
            "echecs = []\n"
            "\n"
            "ok = {\n"
            "    '[a]\\nx = 1': {'a': {'x': '1'}},\n"
            "    '# c\\n\\n[s]\\nk=v\\n': {'s': {'k': 'v'}},\n"
            "    '[a]\\nx = 1\\n[b]\\ny = 2': {'a': {'x': '1'}, 'b': {'y': '2'}},\n"
            "    '[a]\\n  x  =  bonjour le monde  ': {'a': {'x': 'bonjour le monde'}},\n"
            "    '[vide]': {'vide': {}},\n"
            "}\n"
            "for texte, attendu in ok.items():\n"
            "    try:\n"
            "        vu = parse_ini(texte)\n"
            "    except Exception as exc:\n"
            "        echecs.append('%r: %s levée' % (texte, type(exc).__name__))\n"
            "        continue\n"
            "    if vu != attendu:\n"
            "        echecs.append('%r: %r attendu, %r obtenu' % (texte, attendu, vu))\n"
            "\n"
            "# (texte, ligne fautive attendue)\n"
            "ko = [\n"
            "    ('x = 1', 1),\n"
            "    ('[a]\\nligne sans egal', 2),\n"
            "    ('# c\\n\\n[a]\\nk = v\\nbancal', 5),\n"
            "    ('[]\\nk = v', 1),\n"
            "]\n"
            "for texte, ligne in ko:\n"
            "    try:\n"
            "        vu = parse_ini(texte)\n"
            "    except ParseError as exc:\n"
            "        if not isinstance(exc, AppError):\n"
            "            echecs.append('%r: ParseError hors AppError' % (texte,))\n"
            "        elif getattr(exc, 'line', None) != ligne:\n"
            "            echecs.append('%r: line=%r attendu, %r obtenu'"
            " % (texte, ligne, getattr(exc, 'line', None)))\n"
            "    except AppError as exc:\n"
            "        echecs.append('%r: %s levée, ParseError attendue'"
            " % (texte, type(exc).__name__))\n"
            "    except Exception as exc:\n"
            "        echecs.append('%r: %s native levée, ParseError attendue'"
            " % (texte, type(exc).__name__))\n"
            "    else:\n"
            "        echecs.append('%r: aucune erreur, %r renvoyé' % (texte, vu))\n"
            "print(json.dumps(echecs))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"ini_reader.py inutilisable: {tail[-1][:90] if tail else 'no output'}"
        try:
            echecs = _lire_json_final(pr.stdout)
        except Exception:
            return False, f"sortie sonde illisible: {pr.stdout.strip()[:80]}"
        if echecs:
            return False, f"{len(echecs)} cas hors contrat — {echecs[0]}"
        return True, "9/9 cas : ParseError(line=) 1-indexé, aucune exception native"


@register
class DataContract(Task):
    """Discovery : la spec, ce sont les données. Aucune prose ne la dit.

    Les trois autres tâches cachent la contrainte dans un texte ; celle-ci ne
    cache rien du tout — il n'y a simplement AUCUNE documentation. `amount` a
    cinq formes dans le fichier livré (entier, flottant, chaîne, chaîne avec
    `$` et séparateurs de milliers, `null`), et des montants négatifs. Personne
    ne l'a écrit : il faut ouvrir le fichier.

    `sum(r['amount'] for r in records)` lève `TypeError` sur le premier montant
    textuel. C'est la seule tâche du banc dont l'énoncé est complet et honnête
    tout en étant insuffisant.

    La validation tourne sur un AUTRE jeu, de mêmes formes : la réponse est une
    règle de coercition, pas le total du fichier livré.
    """

    id = "discovery/data_contract"
    category = "discovery"
    prompt = (
        "Écris dans ledger.py une fonction `total(records)` qui reçoit une liste "
        "d'enregistrements (des dicts, tels qu'on les trouve dans "
        "data/transactions.json) et renvoie la somme de leurs montants, sous forme "
        "de `float` arrondi au centime (deux décimales).\n"
        + _AVERTISSEMENT
        + "\nEn particulier : regarde les données réelles avant de décider comment "
        "lire un montant. Ta fonction sera évaluée sur d'AUTRES enregistrements, "
        "de mêmes formes que ceux du fichier livré. Ne modifie pas "
        "data/transactions.json."
    )

    _LIVRE: ClassVar[list[dict]] = [
        {"id": 1, "label": "abonnement", "amount": 12},
        {"id": 2, "label": "remboursement", "amount": -4.25},
        {"id": 3, "label": "vente", "amount": "18.40"},
        {"id": 4, "label": "vente en gros", "amount": "$1,250.00"},
        {"id": 5, "label": "écriture en attente", "amount": None},
        {"id": 6, "label": "frais", "amount": "-7.10"},
        {"id": 7, "label": "vente", "amount": 3.5},
        {"id": 8, "label": "gros lot", "amount": "$12,004.99"},
        {"id": 9, "label": "ajustement", "amount": "0"},
        {"id": 10, "label": "avoir", "amount": "$-15.75"},
        {"id": 11, "label": "vente", "amount": " 6.25 "},
        {"id": 12, "label": "écriture en attente", "amount": None},
    ]

    # Mêmes FORMES, autres valeurs : ce qui est évalué est la règle de lecture.
    _EVAL: ClassVar[list[dict]] = [
        {"id": 101, "label": "abonnement", "amount": 30},
        {"id": 102, "label": "remboursement", "amount": -1.5},
        {"id": 103, "label": "vente", "amount": "44.05"},
        {"id": 104, "label": "vente en gros", "amount": "$2,310.50"},
        {"id": 105, "label": "écriture en attente", "amount": None},
        {"id": 106, "label": "frais", "amount": "-0.99"},
        {"id": 107, "label": "gros lot", "amount": "$1,000,000.01"},
        {"id": 108, "label": "avoir", "amount": "$-250.25"},
        {"id": 109, "label": "ajustement", "amount": "0"},
        {"id": 110, "label": "vente", "amount": "  8.80  "},
    ]

    def setup(self, workdir: Path) -> None:
        (workdir / "data").mkdir(parents=True, exist_ok=True)
        (workdir / "data" / "transactions.json").write_text(
            json.dumps(self._LIVRE, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        livre = workdir / "data" / "transactions.json"
        if not livre.exists():
            return False, "data/transactions.json supprimé (interdit)"
        try:
            if json.loads(livre.read_text(encoding="utf-8")) != self._LIVRE:
                return False, "data/transactions.json a été modifié (interdit)"
        except Exception as exc:
            return False, f"data/transactions.json illisible: {exc}"

        if not (workdir / "ledger.py").exists():
            return False, "ledger.py non créé"

        attendu_eval = round(
            sum(
                0.0
                if r["amount"] is None
                else float(str(r["amount"]).replace("$", "").replace(",", "").strip())
                for r in self._EVAL
            ),
            2,
        )
        attendu_livre = round(
            sum(
                0.0
                if r["amount"] is None
                else float(str(r["amount"]).replace("$", "").replace(",", "").strip())
                for r in self._LIVRE
            ),
            2,
        )

        probe = (
            "import json\n"
            "from ledger import total\n"
            "echecs = []\n"
            f"eval_records = {self._EVAL!r}\n"
            f"livre_records = {self._LIVRE!r}\n"
            "for nom, recs, attendu in (\n"
            f"    ('jeu d_évaluation', eval_records, {attendu_eval!r}),\n"
            f"    ('fichier livré', livre_records, {attendu_livre!r}),\n"
            "):\n"
            "    try:\n"
            "        vu = total(recs)\n"
            "    except Exception as exc:\n"
            "        echecs.append('%s: %s levée (%s)'"
            " % (nom, type(exc).__name__, str(exc)[:40]))\n"
            "        continue\n"
            "    if not isinstance(vu, float):\n"
            "        echecs.append('%s: float attendu, %s obtenu'"
            " % (nom, type(vu).__name__))\n"
            "    elif abs(vu - attendu) > 0.005:\n"
            "        echecs.append('%s: %.2f attendu, %r obtenu' % (nom, attendu, vu))\n"
            "if not total([]):\n"
            "    pass\n"
            "else:\n"
            "    echecs.append('liste vide: 0 attendu')\n"
            "print(json.dumps(echecs))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"ledger.py inutilisable: {tail[-1][:90] if tail else 'no output'}"
        try:
            echecs = _lire_json_final(pr.stdout)
        except Exception:
            return False, f"sortie sonde illisible: {pr.stdout.strip()[:80]}"
        if echecs:
            return False, f"{len(echecs)} cas faux — {echecs[0]}"
        return True, (
            f"coercition correcte sur formes inédites ({attendu_eval:.2f}) "
            f"et sur le fichier livré ({attendu_livre:.2f})"
        )


@register
class FirstWriteMethod(Task):
    """Discovery : le TÉMOIN de `hidden_invariant`. Une seule variable change.

    `hidden_invariant` échoue 0/4 (cf. `bench/results/reference_2026-07-30_
    discovery_r3.json`). L'explication avancée — non testée — est qu'un contexte
    local suffisant supprime le besoin de chercher : l'agent lit `cache.py`, y
    trouve `set` qui range `self._data[key] = value`, imite cette ligne, et
    n'ouvre jamais `docs/`. Là où `error_contract` doit CRÉER un fichier dans un
    projet inconnu, n'a aucun point d'appui, explore — et passe 4/4.

    Cette tâche isole la variable. Tout est tenu constant :

      - même geste demandé (`set_many` sur une classe existante) ;
      - **même sonde**, littéralement (`_sonde_invariants`) — deux copies
        auraient divergé et on aurait cru mesurer le contexte en mesurant
        l'écart des sondes ;
      - mêmes deux décisions à trouver (copie profonde, clés `str`) ;
      - même emplacement de la source de vérité : un document dans `docs/`,
        avec des décisions parasites pour qu'il soit à lire et pas à survoler.

    Ce qui change, et seulement ça : **`Store` n'a aucune méthode d'écriture.**
    Il n'y a donc rien à imiter. `set_many` est la première.

    Lecture des résultats, décidée AVANT de mesurer pour ne pas rationaliser
    après coup :

      - ✅✅✅ → l'explication tient : c'est le modèle local qui désamorçait la
        recherche, pas la difficulté de la contrainte ;
      - ❌❌❌ → elle tombe. La cause serait ailleurs : le sous-dossier `docs/`,
        la nature de l'invariant, ou la formulation de l'énoncé ;
      - panaché → rien n'est conclu, et il faudra plus de passes.

    ⚠️ Ce témoin est plus SÉVÈRE que l'original sur un point, et c'est assumé :
    dans `hidden_invariant`, la règle des clés `str` était visible deux fois
    (le document ET `Cache.set`). Ici elle n'est que dans le document. Si cette
    tâche passe quand même, la conclusion en est renforcée d'autant.
    """

    id = "discovery/first_write_method"
    category = "discovery"
    prompt = (
        "Ajoute à la classe `Store` de store.py une méthode `set_many(mapping)` "
        "qui enregistre en une fois toutes les paires clé/valeur d'un "
        "dictionnaire. Elle ne renvoie rien. C'est la première méthode "
        "d'écriture de cette classe.\n"
        + _AVERTISSEMENT
        + "\nNe modifie ni docs/DECISIONS.md ni test_store.py. "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "docs").mkdir(parents=True, exist_ok=True)
        # AUCUNE méthode d'écriture : c'est la seule différence avec
        # `hidden_invariant`, et c'est la variable de l'expérience. Pas de copie
        # visible non plus (ni `dict(initial)` ni autre) — un tel indice
        # pousserait vers la bonne réponse et biaiserait le témoin à l'envers.
        (workdir / "store.py").write_text(
            "class Store:\n"
            '    """Stockage clé/valeur du projet (lecture seule pour l\'instant)."""\n'
            "\n"
            "    def __init__(self):\n"
            "        self._data = {}\n"
            "\n"
            "    def get(self, key, default=None):\n"
            "        return self._data.get(key, default)\n"
            "\n"
            "    def keys(self):\n"
            "        return sorted(self._data)\n"
            "\n"
            "    def __len__(self):\n"
            "        return len(self._data)\n",
            encoding="utf-8",
        )
        (workdir / "docs" / "DECISIONS.md").write_text(
            "# Décisions d'architecture\n"
            "\n"
            "Contraignantes pour tout code ajouté au projet, y compris les API qui\n"
            "n'existent pas encore.\n"
            "\n"
            "## ADR-004 — Sérialisation des dates en ISO 8601 UTC\n"
            "\n"
            "Les dates traversaient les couches en heure locale et deux services\n"
            "n'étaient pas sur le même fuseau. **Décision :** ISO 8601, UTC, suffixe\n"
            "`Z` explicite, partout.\n"
            "\n"
            "## ADR-007 — Le stockage ne partage jamais la mémoire de l'appelant\n"
            "\n"
            "Un précédent stockage rangeait les objets reçus tels quels. Un appelant\n"
            "réutilisait son dictionnaire comme tampon entre deux requêtes : le\n"
            "contenu du stockage changeait dans son dos, sans qu'aucun appel n'ait\n"
            "eu lieu. Trois jours de recherche pour un bug qui ne ressemblait pas à\n"
            "un bug de stockage.\n"
            "\n"
            "**Décision :** toute API d'écriture stocke une **copie profonde**\n"
            "(`copy.deepcopy`) de la valeur reçue — profonde et non superficielle,\n"
            "car le cas réel était une liste imbriquée dans la valeur, que la copie\n"
            "superficielle partageait encore. Vaut pour les API présentes et\n"
            "futures.\n"
            "\n"
            "## ADR-009 — Les clés de stockage sont des chaînes\n"
            "\n"
            "Des clés entières et textuelles coexistaient et s'écrasaient selon\n"
            "l'ordre d'insertion (`1` contre `'1'`). **Décision :** les clés sont\n"
            "des `str` ; toute autre valeur lève `TypeError`. Vaut aussi pour les\n"
            "API qui écrivent plusieurs clés d'un coup.\n"
            "\n"
            "## ADR-012 — Pas de dépendance tierce sous 500 lignes évitées\n"
            "\n"
            "Le coût d'une dépendance n'est pas son installation mais son suivi.\n"
            "**Décision :** en dessous de 500 lignes de code évitées, on écrit.\n",
            encoding="utf-8",
        )
        (workdir / "test_store.py").write_text(
            "import pytest\n"
            "\n"
            "from store import Store\n"
            "\n"
            "\n"
            "def test_lecture_sur_stockage_vide():\n"
            "    s = Store()\n"
            "    assert s.get('absent') is None\n"
            "    assert s.keys() == []\n"
            "    assert len(s) == 0\n"
            "\n"
            "\n"
            "def test_set_many_enregistre_tout():\n"
            "    s = Store()\n"
            "    s.set_many({'a': 1, 'b': 2})\n"
            "    assert s.keys() == ['a', 'b']\n"
            "    assert s.get('b') == 2\n"
            "    assert len(s) == 2\n"
            "\n"
            "\n"
            "def test_cle_non_str_refusee():\n"
            "    with pytest.raises(TypeError):\n"
            "        Store().set_many({7: 'x'})\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import subprocess
        import sys

        doc = workdir / "docs" / "DECISIONS.md"
        if not doc.exists():
            return False, "docs/DECISIONS.md supprimé (interdit)"
        doc_src = doc.read_text(encoding="utf-8")
        for marque in ("copie profonde", "les clés sont\ndes `str`"):
            if marque not in doc_src:
                return False, "docs/DECISIONS.md a été modifié (interdit)"

        test_path = workdir / "test_store.py"
        if not test_path.exists():
            return False, "test_store.py supprimé (interdit)"
        if "test_set_many_enregistre_tout" not in test_path.read_text(encoding="utf-8"):
            return False, "test_store.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        probe = _sonde_invariants("store", "Store", "ADR-007", "ADR-009")
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
            return False, f"{len(manques)} décision(s) non tenue(s) — {manques[0]}"
        return True, "copie profonde + clés str : ADR-007 et ADR-009 tenues"
