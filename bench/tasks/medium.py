"""Tâches medium — refactor léger ou multi-fichier, < 2 min attendus.

Chaque tâche est DISCRIMINANTE : la fixture brute échoue validate(), seule une
vraie transformation la fait passer. Validation 100 % stdlib (ast + exécution
subprocess), avec gardes anti-triche (no-op, hardcode, dépendance optionnelle).
"""

from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


@register
class ExtractFunction(Task):
    """Refactor : un bloc de calcul identique est dupliqué dans 2 fonctions.

    L'agent doit extraire ce bloc dans une fonction réutilisable et l'appeler
    depuis les deux endroits, sans changer le comportement observable.
    """

    id = "medium/extract_function"
    category = "medium"
    prompt = (
        "Le fichier billing.py contient deux fonctions, `facture_pro` et "
        "`facture_perso`, qui partagent EXACTEMENT le même bloc de calcul "
        "(remise de 10%, puis TVA de 20%, arrondi à 2 décimales). "
        "Factorise ce bloc dupliqué : crée UNE seule nouvelle fonction "
        "réutilisable qui fait ce calcul, puis fais en sorte que `facture_pro` "
        "et `facture_perso` l'appellent toutes les deux. "
        "Garde les deux fonctions `facture_pro` et `facture_perso` (mêmes noms, "
        "même signature `(montant)`) et NE change PAS leurs résultats."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "billing.py").write_text(
            "def facture_pro(montant):\n"
            "    remise = montant * 0.10\n"
            "    net = montant - remise\n"
            "    tva = net * 0.20\n"
            "    return round(net + tva, 2)\n"
            "\n"
            "def facture_perso(montant):\n"
            "    remise = montant * 0.10\n"
            "    net = montant - remise\n"
            "    tva = net * 0.20\n"
            "    return round(net + tva, 2)\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        path = workdir / "billing.py"
        if not path.exists():
            return False, "billing.py introuvable"
        src = path.read_text(encoding="utf-8")

        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"billing.py ne compile pas: {exc.msg}"

        # Fonctions top-level (nom -> noeud AST)
        top_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        # Les deux fonctions publiques d'origine doivent rester (pas de suppression)
        for needed in ("facture_pro", "facture_perso"):
            if needed not in top_funcs:
                return False, f"fonction `{needed}` disparue (interdit)"
        # Une nouvelle fonction partagée doit avoir été extraite (>=3 defs au total)
        if len(top_funcs) < 3:
            return False, (f"aucune fonction extraite (vu {len(top_funcs)} def, attendu >=3)")

        public = {"facture_pro", "facture_perso"}
        helpers = {name for name in top_funcs if name not in public}

        def calls_in(fn: ast.FunctionDef) -> set:
            """Noms (simples ou attribut) appelés dans le corps de `fn`."""
            names: set = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Name):
                        names.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        names.add(f.attr)
            return names

        def has_inlined_tva(fn: ast.FunctionDef) -> bool:
            """True si `fn` contient le calcul TVA inliné (x * 0.20),
            robuste aux espaces et au renommage des variables."""
            for node in ast.walk(fn):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                    for side in (node.left, node.right):
                        if (
                            isinstance(side, ast.Constant)
                            and isinstance(side.value, (int, float))
                            and abs(side.value - 0.20) < 1e-9
                        ):
                            return True
            return False

        # Factorisation RÉELLE : chaque fonction publique doit déléguer à un
        # helper extrait (appel) ET ne plus contenir le calcul TVA inliné.
        for pubname in ("facture_pro", "facture_perso"):
            fn = top_funcs[pubname]
            if not (calls_in(fn) & helpers):
                return False, (
                    f"`{pubname}` n'appelle aucune fonction extraite (factorisation absente)"
                )
            if has_inlined_tva(fn):
                return False, (f"`{pubname}` contient encore le calcul TVA inliné (non factorisé)")

        # Le calcul TVA doit avoir été DÉPLACÉ dans un helper effectivement appelé
        # (pas seulement supprimé des fonctions publiques).
        called_helpers = (
            calls_in(top_funcs["facture_pro"]) | calls_in(top_funcs["facture_perso"])
        ) & helpers
        if sum(1 for h in called_helpers if has_inlined_tva(top_funcs[h])) < 1:
            return False, ("le calcul TVA n'a pas été déplacé dans une fonction partagée")

        # Comportement préservé : un driver stdlib appelle les 2 fonctions
        driver = (
            "import billing\n"
            "vals = [(100, 108.0), (250, 270.0), (0, 0.0)]\n"
            "for m, exp in vals:\n"
            "    assert billing.facture_pro(m) == exp, "
            "(m, billing.facture_pro(m), exp)\n"
            "    assert billing.facture_perso(m) == exp, "
            "(m, billing.facture_perso(m), exp)\n"
            "print('OK')\n"
        )
        (workdir / "_driver.py").write_text(driver, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(workdir / "_driver.py")],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, (f"comportement modifié: {tail[-1][:90] if tail else 'no output'}")
        return True, "duplication factorisée (réelle), comportement préservé"


@register
class LoopToComprehension(Task):
    """Refactor : convertir une boucle for/append en list-comprehension.

    La fonction `squares(nums)` construit une liste résultat avec une boucle
    `for` + `.append(...)`. L'agent doit la réécrire en une list-comprehension,
    à comportement identique.

    Validation (RÈGLES DURES) :
    - DISCRIMINANT : la fixture d'origine n'a aucun `ListComp` et contient un
      `for` qui fait `.append()` sur la liste résultat → validate renvoie False.
      Une vraie solution (comprehension) passe les 2 checks statiques + le driver.
    - DÉTERMINISTE : analyse statique via `ast` + un driver exécuté en
      subprocess stdlib (pas de réseau, pas de hasard).
    - STDLIB UNIQUEMENT : ast + subprocess + sys, rien de tiers.
    """

    id = "medium/convert_loop_to_comprehension"
    category = "medium"
    prompt = (
        "Dans le fichier squares.py, la fonction `squares(nums)` construit une "
        "liste avec une boucle `for` et `result.append(...)`. Réécris le corps de "
        "`squares` sous forme d'une list-comprehension (une seule expression "
        "`[... for n in nums]`) qui produit EXACTEMENT le même résultat : la liste "
        "des carrés de chaque élément. Supprime la boucle `for`/`append` et la "
        "variable `result` intermédiaire. Ne change ni le nom de la fonction, ni "
        "sa signature, ni le comportement. Ne modifie aucun autre fichier."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "squares.py").write_text(
            "def squares(nums):\n"
            "    result = []\n"
            "    for n in nums:\n"
            "        result.append(n * n)\n"
            "    return result\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        path = workdir / "squares.py"
        if not path.exists():
            return False, "squares.py manquant"
        src = path.read_text(encoding="utf-8")

        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"squares.py ne parse pas: {exc}"

        func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "squares":
                func = node
                break
        if func is None:
            return False, "fonction `squares` introuvable"

        # 1) Un ListComp doit exister dans le corps de squares.
        has_listcomp = any(isinstance(n, ast.ListComp) for n in ast.walk(func))
        if not has_listcomp:
            return False, "aucune list-comprehension (ListComp) dans `squares`"

        # 2) Plus de boucle (for OU while) contenant un `.append(...)`
        #    (build par boucle). On couvre while pour empêcher une triche
        #    « dummy ListComp + while/append » de passer la statique.
        for n in ast.walk(func):
            if isinstance(n, (ast.For, ast.While)):
                for sub in ast.walk(n):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "append"
                    ):
                        return False, "boucle (for/while)+append encore présente dans `squares`"

        # 3) Driver stdlib : le comportement doit être identique.
        driver = (
            "import json\n"
            "from squares import squares\n"
            "cases = [[1, 2, 3, 4, 5], [], [0, -3, 10], [7]]\n"
            "out = [squares(c) for c in cases]\n"
            "print(json.dumps(out))\n"
        )
        (workdir / "_drv_lc.py").write_text(driver, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(workdir / "_drv_lc.py")],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"driver KO: {tail[-1][:80] if tail else 'no output'}"

        import json as _json

        try:
            got = _json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False, f"sortie driver illisible: {proc.stdout.strip()[:80]}"

        expected = [[1, 4, 9, 16, 25], [], [0, 9, 100], [49]]
        if got != expected:
            return False, f"résultat incorrect: attendu {expected}, vu {got}"

        return True, "list-comprehension correcte, comportement identique"


@register
class AddTypeHints(Task):
    """Annotations de type : 3 fonctions non typées → typage complet.

    Démontre la capacité de l'agent à inférer des types corrects à partir du
    corps des fonctions, sans casser le comportement ni l'import du module.
    Validation 100% stdlib (ast + subprocess), discriminante (la fixture
    échoue, un typage partiel échoue, un typage dégénéré `object`/`Any`
    échoue, seule une solution complète et non-triviale passe).
    """

    id = "medium/add_type_hints"
    category = "medium"
    prompt = (
        "Le module geometry.py contient 3 fonctions sans annotations de type : "
        "`area_rectangle`, `join_names` et `count_vowels`. Ajoute des annotations "
        "de type COMPLÈTES à chacune : un type pour CHAQUE paramètre ET un type de "
        "retour. Les types doivent être cohérents avec ce que fait le corps de la "
        "fonction (pas de `object` ni `Any` fourre-tout). Ne change PAS le "
        "comportement ni les noms ; le module doit toujours s'importer et "
        "fonctionner."
    )

    # Fonctions cibles à typer entièrement (params + retour).
    _TARGETS = ("area_rectangle", "join_names", "count_vowels")

    # Annotations dégénérées interdites : elles satisfont la lettre (« il y a une
    # annotation ») mais défont l'intention (« type cohérent avec le corps »).
    _BANNED_ANNOTATIONS = frozenset({"object", "Any", "typing.Any", "t.Any"})

    def setup(self, workdir: Path) -> None:
        (workdir / "geometry.py").write_text(
            '"""Petit module de fonctions utilitaires, sans annotations de type."""\n'
            "\n"
            "\n"
            "def area_rectangle(width, height):\n"
            "    return width * height\n"
            "\n"
            "\n"
            "def join_names(names, separator):\n"
            "    return separator.join(names)\n"
            "\n"
            "\n"
            "def count_vowels(text):\n"
            '    return sum(1 for ch in text if ch.lower() in "aeiou")\n',
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        path = workdir / "geometry.py"
        if not path.exists():
            return False, "geometry.py absent"
        src = path.read_text(encoding="utf-8")

        # 1) Analyse statique : chaque fonction cible doit avoir une annotation
        #    NON DÉGÉNÉRÉE sur tous ses paramètres + une annotation de retour.
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"syntaxe invalide: {exc}"

        funcs = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        missing = set(self._TARGETS) - set(funcs)
        if missing:
            return False, f"fonction(s) manquante(s): {sorted(missing)}"

        def _ann_str(annotation: ast.expr) -> str:
            try:
                return ast.unparse(annotation).strip()
            except Exception:
                return ""

        for name in self._TARGETS:
            node = funcs[name]
            a = node.args
            params = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            if a.vararg:
                params.append(a.vararg)
            if a.kwarg:
                params.append(a.kwarg)
            for p in params:
                if p.arg in ("self", "cls"):
                    continue
                if p.annotation is None:
                    return False, f"{name}: paramètre `{p.arg}` sans annotation"
                if _ann_str(p.annotation) in self._BANNED_ANNOTATIONS:
                    return False, (
                        f"{name}: paramètre `{p.arg}` annoté de façon "
                        f"dégénérée (object/Any interdit)"
                    )
            if node.returns is None:
                return False, f"{name}: annotation de retour manquante"
            if _ann_str(node.returns) in self._BANNED_ANNOTATIONS:
                return False, (f"{name}: annotation de retour dégénérée (object/Any interdit)")

        # 2) Le module doit toujours s'importer (annotations non cassantes).
        proc = subprocess.run(
            [sys.executable, "-c", "import geometry"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"module ne s'importe plus: {tail[-1][:80] if tail else 'no output'}"

        # 3) Le comportement doit être préservé (anti-régression).
        check = (
            "import geometry as g; "
            "assert g.area_rectangle(3, 4) == 12; "
            "assert g.join_names(['a', 'b'], '-') == 'a-b'; "
            "assert g.count_vowels('hello') == 2"
        )
        proc2 = subprocess.run(
            [sys.executable, "-c", check],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc2.returncode != 0:
            tail = (proc2.stdout + proc2.stderr).strip().splitlines()
            return False, f"comportement cassé: {tail[-1][:80] if tail else 'no output'}"

        return True, "3/3 fonctions typées (non dégénéré), module fonctionnel"


@register
class AddCliArg(Task):
    """Ajout d'option CLI : un script argparse n'a qu'un argument, l'agent doit
    ajouter un drapeau booleen --verbose qui, lorsqu'il est passe, imprime une
    sortie supplementaire en plus de la sortie normale.

    Validation discriminante : on lance `script.py` sans option puis avec
    `--verbose`, les deux doivent reussir (returncode 0), et la sortie de
    `--verbose` doit DIFFERER (et etre plus riche) que la sortie par defaut.
    On verifie aussi par analyse statique (ast) que argparse declare bien
    `--verbose` ET que l'option existante `--name` n'a pas ete supprimee, et on
    confirme a l'execution que `--name` change toujours la sortie. Le cas
    « --verbose declare mais ignore » est rejete car les deux sorties seraient
    identiques. Stdlib uniquement (argparse).
    """

    id = "medium/add_cli_arg"
    category = "medium"
    prompt = (
        "Le script script.py est un programme en ligne de commande base sur "
        "argparse : il accepte deja une option `--name` et imprime un message "
        "de salutation. Ajoute une option booleenne `--verbose` (drapeau, "
        "action='store_true'). Quand `--verbose` est passe, le script doit "
        "imprimer des informations supplementaires (au moins une ligne de plus) "
        "EN PLUS du message normal ; sans `--verbose`, le comportement et la "
        "sortie doivent rester inchanges. Le script doit se lancer sans erreur "
        "dans les deux cas : `python script.py` et `python script.py --verbose`. "
        "Ne casse pas l'option `--name` existante."
    )

    def setup(self, workdir: Path) -> None:
        # Fixture : script argparse avec UN seul argument (--name), pas de --verbose.
        (workdir / "script.py").write_text(
            "import argparse\n"
            "\n"
            "\n"
            "def build_parser():\n"
            "    parser = argparse.ArgumentParser(description='Salue une personne.')\n"
            "    parser.add_argument('--name', default='monde', help='nom a saluer')\n"
            "    return parser\n"
            "\n"
            "\n"
            "def main():\n"
            "    parser = build_parser()\n"
            "    args = parser.parse_args()\n"
            "    print(f'Bonjour, {args.name}!')\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import re
        import subprocess
        import sys

        path = workdir / "script.py"
        if not path.exists():
            return False, "script.py absent"
        src = path.read_text(encoding="utf-8")

        # 1) Analyse statique : un add_argument doit declarer le drapeau demande.
        def declares_arg(source: str, flag: str) -> bool:
            try:
                tree = ast.parse(source)
            except SyntaxError:
                return False
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == flag:
                        return True
            # filet regex au cas ou add_argument serait construit autrement
            return bool(
                re.search(
                    r"""add_argument\(\s*['"]""" + re.escape(flag) + r"""['"]""",
                    source,
                )
            )

        # argparse doit declarer --verbose.
        if not declares_arg(src, "--verbose"):
            return False, "argparse ne declare pas --verbose"
        # ... sans supprimer l'option existante --name (exigence du prompt).
        if not declares_arg(src, "--name"):
            return False, "l'option --name existante a ete cassee (non declaree)"

        def run(extra: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(path), *extra],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workdir,
            )

        # 2) Le run par defaut doit reussir.
        default = run([])
        if default.returncode != 0:
            tail = (default.stderr or default.stdout).strip()[:120]
            return False, f"run par defaut KO (rc={default.returncode}): {tail}"

        # 3) L'option existante --name doit toujours fonctionner (anti-regression).
        named = run(["--name", "Alice"])
        if named.returncode != 0:
            tail = (named.stderr or named.stdout).strip()[:120]
            return False, f"run --name KO (rc={named.returncode}): {tail}"
        if "Alice" not in named.stdout:
            return False, "--name ne change plus la sortie (option existante cassee)"

        # 4) Le run --verbose doit reussir.
        verbose = run(["--verbose"])
        if verbose.returncode != 0:
            tail = (verbose.stderr or verbose.stdout).strip()[:120]
            return False, f"run --verbose KO (rc={verbose.returncode}): {tail}"

        # 5) La sortie --verbose doit differer ET etre plus riche (anti-cheat :
        #    drapeau declare mais ignore -> sorties identiques -> rejet).
        if verbose.stdout == default.stdout:
            return False, "sortie --verbose identique a la sortie par defaut"
        if len(verbose.stdout) <= len(default.stdout):
            return False, "sortie --verbose pas plus riche que la sortie par defaut"

        return True, "drapeau --verbose declare + sortie enrichie + --name preserve"


@register
class JsonToDataclass(Task):
    """Refactor : un module représente un produit comme un dict ; l'agent doit
    introduire une @dataclass typée `Product` et l'utiliser (constructeur +
    accès par attribut), sans changer la sortie observable de `describe`."""

    id = "medium/json_to_dataclass"
    category = "medium"
    prompt = (
        "Le fichier inventory.py représente un produit comme un simple dict "
        "(`make_product`) et y accède par clés dans `describe`. Refactore ce module "
        "pour introduire une @dataclass nommée `Product` avec exactement ces champs "
        "typés : `name: str`, `price: float`, `quantity: int`. `make_product(name, "
        "price, quantity)` doit RETOURNER une instance de `Product` (plus un dict), "
        "et `describe(product)` doit accéder aux données par attributs "
        "(`product.name`, etc.) au lieu de clés. Garde la chaîne renvoyée par "
        "`describe` IDENTIQUE pour les mêmes valeurs. Ne change pas les noms des "
        "fonctions ni leurs signatures publiques."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "inventory.py").write_text(
            "def make_product(name, price, quantity):\n"
            '    return {"name": name, "price": price, "quantity": quantity}\n'
            "\n"
            "\n"
            "def describe(product):\n"
            "    return (\n"
            "        f\"{product['name']}: {product['quantity']} x \"\n"
            "        f\"{product['price']:.2f} EUR\"\n"
            "    )\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys
        import textwrap

        path = workdir / "inventory.py"
        if not path.exists():
            return False, "inventory.py introuvable"
        src = path.read_text(encoding="utf-8")

        # --- 1) Analyse statique : une @dataclass Product avec les bons champs typés
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"inventory.py ne parse pas: {exc}"

        product_cls = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Product":
                product_cls = node
                break
        if product_cls is None:
            return False, "classe `Product` absente"

        # décorateur @dataclass (dataclass / dataclasses.dataclass, avec ou sans args)
        def _is_dataclass_deco(dec: ast.expr) -> bool:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name):
                return target.id == "dataclass"
            if isinstance(target, ast.Attribute):
                return target.attr == "dataclass"
            return False

        if not any(_is_dataclass_deco(d) for d in product_cls.decorator_list):
            return False, "`Product` n'est pas décorée @dataclass"

        # champs typés attendus : EXACTEMENT name:str, price:float, quantity:int
        expected = {"name": "str", "price": "float", "quantity": "int"}
        found: dict[str, str] = {}
        for stmt in product_cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                ann = stmt.annotation
                ann_name = (
                    ann.id
                    if isinstance(ann, ast.Name)
                    else ann.attr
                    if isinstance(ann, ast.Attribute)
                    else None
                )
                if ann_name is not None:
                    found[stmt.target.id] = ann_name
        for fname, ftype in expected.items():
            if fname not in found:
                return False, f"champ typé `{fname}` manquant dans Product"
            if found[fname] != ftype:
                return False, (f"champ `{fname}` annoté `{found[fname]}`, attendu `{ftype}`")
        extra = sorted(set(found) - set(expected))
        if extra:
            return False, f"champ(s) inattendu(s) dans Product: {extra}"

        # interdit l'ancien accès par clé sur la variable `product` (ex: product['name'])
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "product"
            ):
                return False, "accès par clé sur `product` encore présent (ex: product['name'])"

        # --- 2) Exécution stdlib : make_product renvoie un Product, describe lit par attribut.
        # Plusieurs vecteurs de valeurs => un describe en dur ne peut pas tous les satisfaire.
        driver = textwrap.dedent(
            """
            import inventory
            from dataclasses import is_dataclass

            cases = [
                ("Stylo", 1.5, 3, "Stylo: 3 x 1.50 EUR"),
                ("Cahier", 12.0, 7, "Cahier: 7 x 12.00 EUR"),
                ("Gomme", 0.99, 1, "Gomme: 1 x 0.99 EUR"),
            ]
            for name, price, qty, exp in cases:
                p = inventory.make_product(name, price, qty)
                assert is_dataclass(p) and not isinstance(p, type), \\
                    "make_product ne renvoie pas une instance de dataclass"
                assert type(p).__name__ == "Product", \\
                    f"type attendu Product, vu {type(p).__name__}"
                assert p.name == name, "attribut name KO"
                assert p.price == price, "attribut price KO"
                assert p.quantity == qty, "attribut quantity KO"
                out = inventory.describe(p)
                assert out == exp, f"describe a change: {out!r} != {exp!r}"
            print("OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"driver KO: {tail[-1][:120] if tail else 'no output'}"
        if "OK" not in proc.stdout:
            return False, f"driver sortie inattendue: {proc.stdout.strip()[:80]}"
        return True, "dataclass Product typée + accès par attribut OK"


@register
class SplitModule(Task):
    """Découpage en modules cohérents : big.py mélange 2 responsabilités.

    Démontre le refactor multi-fichier : l'agent doit déplacer chaque groupe
    de fonctions dans son module dédié ET réparer les imports de main.py.
    validate = analyse statique (ast) des defs déplacées + exécution stdlib
    de main.py (returncode 0) qui importe les noms déplacés.
    """

    id = "medium/split_module"
    category = "medium"
    prompt = (
        "Le fichier big.py mélange DEUX responsabilités distinctes : des fonctions "
        "d'arithmétique (`add`, `multiply`) et des fonctions de chaînes (`shout`, "
        "`reverse_text`). Sépare-le en deux modules cohérents :\n"
        "- arithmetic.py : doit contenir les fonctions `add` et `multiply` ;\n"
        "- strings.py : doit contenir les fonctions `shout` et `reverse_text`.\n"
        "Supprime ces quatre fonctions de big.py (tu peux supprimer big.py ou le "
        "vider). Mets à jour main.py pour qu'il importe `add` et `multiply` depuis "
        "arithmetic, et `shout` et `reverse_text` depuis strings. Ne change PAS le "
        "comportement de main.py ni ses appels. Lance `python main.py` pour vérifier."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "big.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
            "\n"
            "def shout(text):\n"
            "    return text.upper() + '!'\n"
            "\n"
            "def reverse_text(text):\n"
            "    return text[::-1]\n",
            encoding="utf-8",
        )
        (workdir / "main.py").write_text(
            "from big import add, multiply, shout, reverse_text\n"
            "\n"
            "def main():\n"
            "    assert add(2, 3) == 5\n"
            "    assert multiply(2, 3) == 6\n"
            "    assert shout('hi') == 'HI!'\n"
            "    assert reverse_text('abc') == 'cba'\n"
            "    print('OK')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        arith = workdir / "arithmetic.py"
        strings = workdir / "strings.py"
        main = workdir / "main.py"

        if not arith.exists():
            return False, "arithmetic.py manquant"
        if not strings.exists():
            return False, "strings.py manquant"
        if not main.exists():
            return False, "main.py manquant"

        def _defs(path: Path) -> set[str]:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                raise ValueError(f"{path.name}: syntaxe invalide ({exc})") from exc
            return {
                n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

        try:
            arith_defs = _defs(arith)
            strings_defs = _defs(strings)
            big = workdir / "big.py"
            big_defs = _defs(big) if big.exists() else set()
        except ValueError as exc:
            return False, str(exc)

        # arithmetic.py doit définir add et multiply, PAS les fonctions string
        if not {"add", "multiply"} <= arith_defs:
            return False, f"arithmetic.py ne définit pas add+multiply (vu {sorted(arith_defs)})"
        if arith_defs & {"shout", "reverse_text"}:
            return False, "arithmetic.py contient des fonctions string (mauvais découpage)"

        # strings.py doit définir shout et reverse_text, PAS les fonctions arith
        if not {"shout", "reverse_text"} <= strings_defs:
            return (
                False,
                f"strings.py ne définit pas shout+reverse_text (vu {sorted(strings_defs)})",
            )
        if strings_defs & {"add", "multiply"}:
            return False, "strings.py contient des fonctions arithmétiques (mauvais découpage)"

        # big.py ne doit plus définir les fonctions déplacées
        moved = {"add", "multiply", "shout", "reverse_text"}
        if big_defs & moved:
            return (
                False,
                f"big.py contient encore des fonctions déplacées ({sorted(big_defs & moved)})",
            )

        # main.py doit tourner (returncode 0) en important les noms déplacés
        proc = subprocess.run(
            [sys.executable, str(main)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"main.py KO: {tail[-1][:100] if tail else 'no output'}"
        return True, "module découpé en 2 + main.py tourne"


@register
class AddLogging(Task):
    """Ajouter du logging structuré à un script multi-étapes qui n'en a aucun.

    Le script enchaîne plusieurs fonctions (normalisation, calcul, remise) et
    ne contient AUCUN print : l'agent doit instrumenter le code *depuis zéro*
    avec le module logging standard (import logging, logger module-level via
    getLogger(__name__), et des appels logger.<niveau>(...) aux étapes clés).

    Validation 100% stdlib : analyse statique (ast) pour les critères logging,
    PUIS anti-triche en deux temps — (1) ast confirme que les 4 fonctions métier
    sont toujours présentes, (2) un harness importe `traiter_commande` et vérifie
    que le résultat reste 90.0. Cela empêche l'agent de "gagner" en écrasant le
    script par un stub bidon ou en vidant la logique.
    """

    id = "medium/add_logging"
    category = "medium"
    prompt = (
        "Le script `pipeline.py` enchaîne plusieurs étapes de traitement mais "
        "n'a AUCUNE instrumentation : impossible de suivre ce qu'il fait à "
        "l'exécution. Ajoute du logging structuré avec le module standard "
        "`logging` :\n"
        "1. `import logging` en tête de fichier ;\n"
        "2. un logger au niveau module : `logger = logging.getLogger(__name__)` ;\n"
        "3. au moins deux appels `logger.info(...)` / `logger.debug(...)` aux "
        "étapes clés (par ex. normalisation des items, calcul du total, "
        "application de la remise).\n"
        "N'utilise PAS `print`. Ne change pas la logique métier ni les valeurs "
        "retournées : le script doit toujours s'exécuter sans erreur "
        "(`python pipeline.py`)."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "pipeline.py").write_text(
            '"""Pipeline de traitement de commandes (multi-étapes, sans logging)."""\n'
            "\n"
            "\n"
            "def normaliser_items(items):\n"
            "    nettoyes = []\n"
            "    for item in items:\n"
            "        nom = item['nom'].strip().lower()\n"
            "        qte = int(item['qte'])\n"
            "        nettoyes.append({'nom': nom, 'qte': qte})\n"
            "    return nettoyes\n"
            "\n"
            "\n"
            "def calculer_total(items, prix_unitaire):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        total += item['qte'] * prix_unitaire\n"
            "    return total\n"
            "\n"
            "\n"
            "def appliquer_remise(total, seuil, taux):\n"
            "    if total >= seuil:\n"
            "        total = total * (1 - taux)\n"
            "    return total\n"
            "\n"
            "\n"
            "def traiter_commande(items, prix_unitaire):\n"
            "    items = normaliser_items(items)\n"
            "    total = calculer_total(items, prix_unitaire)\n"
            "    final = appliquer_remise(total, seuil=100, taux=0.1)\n"
            "    return final\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    commande = [\n"
            "        {'nom': '  Pomme ', 'qte': '30'},\n"
            "        {'nom': 'Poire', 'qte': '20'},\n"
            "    ]\n"
            "    resultat = traiter_commande(commande, prix_unitaire=2)\n"
            "    assert resultat == 90.0, resultat\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        path = workdir / "pipeline.py"
        if not path.exists():
            return False, "pipeline.py manquant"
        src = path.read_text(encoding="utf-8")

        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"syntaxe invalide: {exc}"

        # 0. anti-triche statique : la logique métier doit rester en place
        #    (sinon on pourrait écraser le script par un stub satisfaisant les
        #    seuls checks logging).
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        required_funcs = {
            "normaliser_items",
            "calculer_total",
            "appliquer_remise",
            "traiter_commande",
        }
        missing = required_funcs - funcs
        if missing:
            return False, f"fonctions metier supprimees: {sorted(missing)}"

        # 1. import logging (import logging | from logging import ...)
        imports_logging = any(
            (isinstance(n, ast.Import) and any(a.name == "logging" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "logging")
            for n in ast.walk(tree)
        )
        if not imports_logging:
            return False, "import logging manquant"

        # 2. un appel à getLogger(...) (logging.getLogger ou getLogger importé)
        has_getlogger = any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Attribute) and n.func.attr == "getLogger")
                or (isinstance(n.func, ast.Name) and n.func.id == "getLogger")
            )
            for n in ast.walk(tree)
        )
        if not has_getlogger:
            return False, "logging.getLogger(...) manquant"

        # 3. au moins 2 appels logger.<niveau>(...) (debug/info/warning/error/...)
        log_levels = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
        log_calls = 0
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in log_levels
                and isinstance(n.func.value, ast.Name)
            ):
                log_calls += 1
        if log_calls < 2:
            return False, f"attendu >=2 appels logger.<niveau>(...), vu {log_calls}"

        # 4a. le script tourne toujours (python pipeline.py) -> returncode 0
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            why = tail[-1][:80] if tail else "no output"
            return False, f"script KO (rc={proc.returncode}): {why}"

        # 4b. anti-triche dynamique : la logique métier rend toujours 90.0
        #     (empêche les stubs vides ou un calcul faussé tout en gardant rc=0).
        sentinel = "__BENCH_OK__"
        harness = (
            "import importlib.util as _u\n"
            f"_s=_u.spec_from_file_location('pipeline', r'{path}')\n"
            "_m=_u.module_from_spec(_s); _s.loader.exec_module(_m)\n"
            "_cmd=[{'nom':'  Pomme ','qte':'30'},{'nom':'Poire','qte':'20'}]\n"
            "_r=_m.traiter_commande(_cmd, prix_unitaire=2)\n"
            "assert _r==90.0, _r\n"
            f"print('{sentinel}', _r)\n"
        )
        chk = subprocess.run(
            [sys.executable, "-c", harness],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if chk.returncode != 0 or sentinel not in chk.stdout:
            tail = (chk.stdout + chk.stderr).strip().splitlines()
            why = tail[-1][:80] if tail else "no output"
            return False, f"logique metier alteree (attendu 90.0): {why}"

        return True, f"logging ajoute ({log_calls} appels) + logique metier preservee"


@register
class AddErrorHandling(Task):
    """Ajout de gestion d'erreur contextuelle : une fonction plante sur une
    entrée invalide (int() qui lève ValueError) ; la spec veut qu'elle attrape
    l'erreur et renvoie une valeur par défaut au lieu de remonter l'exception.

    Discriminant : la fixture brute fait planter run.py (returncode != 0).
    Anti-triche : on vérifie qu'un try/except existe DANS la fonction safe_to_int
    elle-même (analyse AST scopée, pas un decoy ailleurs), que run.py est resté
    identique à la fixture (interdiction de déplacer la gestion d'erreur côté
    appelant), et que le cas invalide ne renvoie pas l'entrée brute mais bien une
    valeur par défaut.
    """

    id = "medium/add_error_handling"
    category = "medium"
    prompt = (
        "Dans le fichier parser.py, la fonction `safe_to_int(raw)` plante sur une "
        "entrée non numérique car `int(raw)` lève une ValueError. Modifie-la pour "
        "qu'elle gère l'erreur de façon contextuelle : entoure la conversion d'un "
        "bloc try/except et, en cas d'échec, renvoie une valeur par défaut "
        "(par exemple -1) au lieu de laisser l'exception remonter. Ne renvoie SURTOUT "
        "pas l'entrée brute telle quelle. Ne modifie pas run.py. Le script run.py doit "
        "ensuite s'exécuter sans planter : il doit afficher `bon=42` pour l'entrée "
        "valide '42' et une valeur par défaut pour l'entrée invalide 'abc'. "
        "Lance `python run.py` pour vérifier."
    )

    _EXPECTED_RUN = (
        "from parser import safe_to_int\n"
        "\n"
        "def main():\n"
        "    bon = safe_to_int('42')\n"
        "    print(f'bon={bon}')\n"
        "    mauvais = safe_to_int('abc')\n"
        "    print(f'mauvais={mauvais}')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "parser.py").write_text(
            "def safe_to_int(raw):\n"
            "    # Convertit `raw` en entier. Plante actuellement sur une entree non numerique.\n"
            "    return int(raw)\n",
            encoding="utf-8",
        )
        (workdir / "run.py").write_text(self._EXPECTED_RUN, encoding="utf-8")

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import re
        import subprocess
        import sys

        workdir = Path(workdir).resolve()

        parser_path = workdir / "parser.py"
        if not parser_path.exists():
            return False, "parser.py manquant"
        src = parser_path.read_text(encoding="utf-8")

        # try/except présent DANS la fonction safe_to_int (pas un decoy ailleurs)
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"parser.py invalide: {exc.msg}"

        def _func_has_try(node_tree: ast.AST, name: str) -> bool:
            for node in ast.walk(node_tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return any(isinstance(d, ast.Try) for d in ast.walk(node))
            return False

        if not _func_has_try(tree, "safe_to_int"):
            return False, "pas de bloc try/except dans la fonction safe_to_int"

        # Anti-triche : run.py doit rester identique à la fixture (interdit de
        # déplacer la gestion d'erreur côté appelant).
        run_path = workdir / "run.py"
        if not run_path.exists():
            return False, "run.py manquant"
        run_src = run_path.read_text(encoding="utf-8")
        if run_src != self._EXPECTED_RUN:
            return False, "run.py a ete modifie (interdit)"

        # run.py ne doit plus planter
        proc = subprocess.run(
            [sys.executable, str(run_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return (
                False,
                f"run.py plante (code {proc.returncode}): {tail[-1][:80] if tail else 'no output'}",
            )

        out = proc.stdout
        if "bon=42" not in out:
            return False, f"cas valide incorrect, attendu 'bon=42': {out.strip()[:80]}"
        m = re.search(r"mauvais=(.+)", out)
        if not m:
            return False, f"pas de sortie 'mauvais=' pour le cas invalide: {out.strip()[:80]}"
        sentinel = m.group(1).strip()
        if sentinel == "abc":
            return False, "le cas invalide renvoie l'entree brute, pas une valeur par defaut geree"
        if sentinel in {"", "Traceback"}:
            return False, f"sortie inattendue pour le cas invalide: {sentinel!r}"
        return True, f"erreur geree (bon=42, mauvais={sentinel}), try/except dans safe_to_int"


@register
class FixFailingTest(Task):
    """Fix bug : un test pytest échoue, l'agent doit corriger le CODE (pas le test).

    Démontre l'impact du sandbox loop (Roadmap v2 #3) : avec sandbox auto-exec,
    l'agent voit l'AssertionError dans stderr et corrige immédiatement.
    """

    id = "medium/fix_failing_test"
    category = "medium"
    prompt = (
        "Le test test_calc.py échoue. Lis le test et le module calc.py, identifie "
        "le bug dans calc.py et corrige-le. Ne modifie PAS le test — c'est la "
        "spécification. Lance pytest pour vérifier que tout passe."
    )

    def setup(self, workdir):
        # calc.py : la fonction `multiply` retourne `a + b` au lieu de `a * b`
        (workdir / "calc.py").write_text(
            "def multiply(a, b):\n    # BUG: opérateur incorrect\n    return a + b\n",
            encoding="utf-8",
        )
        (workdir / "test_calc.py").write_text(
            "from calc import multiply\n"
            "\n"
            "def test_multiply_positives():\n"
            "    assert multiply(3, 4) == 12\n"
            "\n"
            "def test_multiply_with_one():\n"
            "    assert multiply(7, 1) == 7\n"
            "\n"
            "def test_multiply_with_zero():\n"
            "    assert multiply(5, 0) == 0\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import subprocess
        import sys

        # Le code corrigé doit faire passer les 3 tests sans modifier test_calc.py
        test_src = (workdir / "test_calc.py").read_text(encoding="utf-8")
        if "multiply(3, 4) == 12" not in test_src:
            return False, "test_calc.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(workdir / "test_calc.py"), "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:80] if tail else 'no output'}"
        if "3 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:80]}"
        return True, "3/3 tests passent, code corrigé"


@register
class MigratePrintToLogger(Task):
    """Multi-fichier : remplacer tous les print() par logger.info() dans le projet.

    Démontre l'impact du retrieval (Roadmap v2 #6) : sans find_relevant_files
    ou find_references, l'agent ne sait pas tous les endroits où print() est utilisé.
    """

    id = "medium/migrate_print_to_logger"
    category = "medium"
    prompt = (
        "Dans ce projet, remplace TOUS les appels `print(...)` par `logger.info(...)`. "
        "Ajoute `import logging` et `logger = logging.getLogger(__name__)` en tête des "
        "fichiers concernés s'ils manquent. N'ajoute pas de print, n'en supprime aucun "
        "par erreur. Couvre tous les fichiers .py du projet."
    )

    def setup(self, workdir):
        # 3 fichiers Python avec des print éparpillés
        (workdir / "app.py").write_text(
            "def greet(name):\n    print(f'Hello, {name}')\n    return name\n",
            encoding="utf-8",
        )
        (workdir / "utils.py").write_text(
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        print('Erreur: division par zéro')\n"
            "        return None\n"
            "    print(f'{a} / {b}')\n"
            "    return a / b\n",
            encoding="utf-8",
        )
        (workdir / "main.py").write_text(
            "from app import greet\n"
            "from utils import divide\n"
            "\n"
            "print('Démarrage')\n"
            "greet('Klody')\n"
            "divide(10, 2)\n"
            "print('Fin')\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import re

        files = ["app.py", "utils.py", "main.py"]
        for name in files:
            src = (workdir / name).read_text(encoding="utf-8")
            # Plus de print(
            if re.search(r"\bprint\s*\(", src):
                return False, f"{name}: print() encore présent"
            # logger.info présent dans les fichiers qui avaient des print
            if "logger.info" not in src:
                return False, f"{name}: pas de logger.info"
            # imports logging présents
            if "import logging" not in src:
                return False, f"{name}: import logging manquant"
            if "getLogger" not in src:
                return False, f"{name}: logger = logging.getLogger(...) manquant"
        return True, "3/3 fichiers migrés correctement"
