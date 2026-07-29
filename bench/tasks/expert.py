"""Tâches expert — au-dessus de `hard`, conçues pour AVOIR de la marge.

Pourquoi ce palier existe : mesuré le 2026-07-29, le banc corrigé rend 20/20 puis
60/60 sur 3 passes, `tool_calls_broken` à 0 partout. Un banc saturé ne départage
plus rien — ni deux modèles, ni deux versions de l'agent. Ces tâches rouvrent de
l'écart.

Ce qui les sépare de `hard` : chacune place un piège où **le réflexe est faux**.
Ce n'est pas « plus de travail », c'est « le premier geste évident échoue ».

| tâche | le réflexe | pourquoi il échoue |
|---|---|---|
| `unhashable_dedup` | `set(items)` | les éléments sont des dicts — non hachables |
| `deadlock_lock_order` | ajouter un verrou | il y en a déjà deux, pris en ordre inverse |
| `cross_module_rename` | renommer la définition | 2 appelants + un alias rétro-compatible |
| `spec_beyond_tests` | rendre les tests verts | ils sont DÉJÀ verts, la spec les dépasse |
| `stream_memory` | optimiser le temps | c'est la mémoire crête qui est plafonnée |

Mêmes règles que `hard` : 100 % stdlib, aucune dépendance tierce pour valider,
fixture qui échoue par construction, gardes AST + exécution réelle contre le
codage en dur.
"""

from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


@register
class UnhashableDedup(Task):
    """Expert : dédoublonnage O(n²) → O(n), mais `set()` ne marche pas.

    Le geste appris pour dédoublonner une liste est `list(set(items))`, ou un
    `seen = set()`. Ici les éléments sont des **dicts** : non hachables, donc
    `set()` lève `TypeError`. Il faut fabriquer une clé canonique.

    Deuxième piège, dans la spec : deux dicts de même contenu écrits dans un
    ordre de clés différent sont le MÊME enregistrement. Un `json.dumps(d)` sans
    `sort_keys=True` — l'autre réflexe — les distingue et laisse passer le
    doublon. `tuple(sorted(d.items()))` ou `json.dumps(d, sort_keys=True)`
    passent ; la version naïve échoue sur un test visible.
    """

    id = "expert/unhashable_dedup"
    category = "expert"
    prompt = (
        "Le fichier records.py contient `dedupe(records)`, qui retire les doublons "
        "d'une liste de dictionnaires en préservant l'ordre de première apparition. "
        "Elle est CORRECTE mais quadratique (`if r not in out` sur une liste), donc "
        "inutilisable sur de grandes listes. Réécris-la en temps linéaire.\n"
        "Contraintes qui font la difficulté :\n"
        "  1. les éléments sont des dictionnaires, donc NON HACHABLES : `set(records)` "
        "lève TypeError. Il te faut une clé canonique par enregistrement ;\n"
        "  2. deux dictionnaires ayant les mêmes paires clé/valeur sont le MÊME "
        "enregistrement, MÊME SI les clés sont écrites dans un ordre différent "
        "(`{'a': 1, 'b': 2}` et `{'b': 2, 'a': 1}` sont des doublons) ;\n"
        "  3. l'ordre de première apparition doit être préservé, et l'élément "
        "conservé doit être le dictionnaire d'origine (pas une copie réordonnée).\n"
        "Les valeurs sont toujours des scalaires (int, str, bool, None). "
        "Ne modifie PAS test_records.py (c'est la spec). "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "records.py").write_text(
            "def dedupe(records):\n"
            '    """Retire les doublons en gardant l\'ordre de première apparition.\n'
            "\n"
            "    Deux enregistrements sont égaux s'ils ont les mêmes paires\n"
            "    clé/valeur, quel que soit l'ordre d'écriture des clés.\n"
            '    """\n'
            "    out = []\n"
            "    for r in records:\n"
            "        # QUADRATIQUE : `in` sur une liste compare r à tout ce qui précède.\n"
            "        if r not in out:\n"
            "            out.append(r)\n"
            "    return out\n",
            encoding="utf-8",
        )
        (workdir / "test_records.py").write_text(
            "from records import dedupe\n"
            "\n"
            "def test_supprime_les_doublons():\n"
            "    src = [{'id': 1}, {'id': 2}, {'id': 1}]\n"
            "    assert dedupe(src) == [{'id': 1}, {'id': 2}]\n"
            "\n"
            "def test_ordre_des_cles_indifferent():\n"
            "    src = [{'a': 1, 'b': 2}, {'b': 2, 'a': 1}]\n"
            "    assert dedupe(src) == [{'a': 1, 'b': 2}]\n"
            "\n"
            "def test_garde_l_objet_d_origine():\n"
            "    premier = {'b': 2, 'a': 1}\n"
            "    out = dedupe([premier, {'a': 1, 'b': 2}])\n"
            "    assert out[0] is premier\n"
            "\n"
            "def test_vide():\n"
            "    assert dedupe([]) == []\n"
            "\n"
            "def test_rien_a_retirer():\n"
            "    src = [{'id': 1}, {'id': 2}, {'id': 3}]\n"
            "    assert dedupe(src) == src\n"
            "\n"
            "def test_valeurs_scalaires_variees():\n"
            "    src = [{'x': None}, {'x': False}, {'x': None}, {'x': 'None'}]\n"
            "    assert dedupe(src) == [{'x': None}, {'x': False}, {'x': 'None'}]\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import json
        import subprocess
        import sys

        test_path = workdir / "test_records.py"
        if not test_path.exists():
            return False, "test_records.py supprimé (interdit)"
        test_src = test_path.read_text(encoding="utf-8")
        for marque in ("test_ordre_des_cles_indifferent", "assert out[0] is premier"):
            if marque not in test_src:
                return False, "test_records.py a été modifié (interdit)"

        src_path = workdir / "records.py"
        if not src_path.exists():
            return False, "records.py absent"
        try:
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return False, f"records.py invalide (SyntaxError): {exc}"
        fn = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "dedupe"),
            None,
        )
        if fn is None:
            return False, "fonction dedupe() absente"
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.posonlyargs}
        for ret in (n for n in ast.walk(fn) if isinstance(n, ast.Return)):
            val = ret.value
            if isinstance(val, (ast.List, ast.Dict)) and not getattr(val, "elts", None):
                continue  # `return []` sur le cas vide : légitime
            if isinstance(val, ast.Constant):
                return False, "dedupe() renvoie une constante (anti-hardcode)"
            if isinstance(val, ast.Name) and val.id in params:
                return False, "dedupe() renvoie son paramètre brut (anti-hardcode)"

        # 1) Correction : la spec doit passer.
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        # 2) Comportement sur grand N + porte de perf. Pire cas = aucun doublon,
        #    donc scan complet : le quadratique ne peut pas s'en sortir par chance.
        #    N=30000 et non 4000 : mesuré, le `in` sur liste compare des dicts
        #    côté C et ne coûte que 0,09 s à N=4000 — sous n'importe quel budget
        #    raisonnable, donc une porte qui ne se serait jamais fermée.
        #    Le contrôle du doublon est SAUTÉ quand le budget est déjà dépassé :
        #    repayer un scan quadratique pour un verdict acquis triplerait le
        #    coût de cette tâche dans la suite de tests.
        budget = 1.5
        probe = (
            "import time, json\n"
            "from records import dedupe\n"
            "N = 30000\n"
            f"BUDGET = {budget}\n"
            "sans_doublon = [{'id': i, 'nom': 'x%d' % i} for i in range(N)]\n"
            "t = time.perf_counter()\n"
            "vu = dedupe(sans_doublon)\n"
            "elapsed = time.perf_counter() - t\n"
            "if len(vu) != N:\n"
            "    print(json.dumps({'erreur': 'faux positif sur grand N'})); raise SystemExit(0)\n"
            "if elapsed < BUDGET:\n"
            "    avec = sans_doublon + [{'nom': 'x0', 'id': 0}]\n"
            "    if len(dedupe(avec)) != N:\n"
            "        print(json.dumps({'erreur': 'doublon non détecté sur grand N'}))\n"
            "        raise SystemExit(0)\n"
            "print(json.dumps({'elapsed': elapsed}))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=300, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"probe KO (grand N): {tail[-1][:90] if tail else 'no output'}"
        try:
            d = json.loads(pr.stdout.strip().splitlines()[-1])
        except Exception:
            return False, f"sortie probe illisible: {pr.stdout.strip()[:80]}"
        if "erreur" in d:
            return False, f"comportement sur grand N : {d['erreur']}"

        elapsed = d["elapsed"]
        if elapsed >= budget:
            return False, f"trop lent ({elapsed:.3f}s >= {budget}s) : toujours quadratique"
        return True, f"clé canonique + linéaire ({elapsed:.3f}s < {budget}s)"


@register
class DeadlockLockOrder(Task):
    """Expert : interblocage par ordre d'acquisition inversé.

    `hard/fix_async_bug` se règle en AJOUTANT un verrou. Ici il y en a déjà deux,
    et c'est justement leur présence qui bloque : `transfer_a_to_b` prend
    `lock_a` puis `lock_b`, `transfer_b_to_a` prend `lock_b` puis `lock_a`. Sous
    `gather`, chacun tient ce que l'autre attend.

    Le réflexe « il manque un verrou » aggrave le problème. Le remède est un
    ORDRE d'acquisition global — un renversement de raisonnement, pas un ajout.

    L'interblocage est déterministe : `await asyncio.sleep(0)` entre les deux
    prises force l'entrelacement à chaque exécution. Il ne s'agit donc pas d'un
    test instable qu'on pourrait voir passer par chance.
    """

    id = "expert/deadlock_lock_order"
    category = "expert"
    prompt = (
        "Le fichier bank.py s'interbloque (deadlock) : `transfer_a_to_b` acquiert "
        "`lock_a` puis `lock_b`, tandis que `transfer_b_to_a` acquiert `lock_b` puis "
        "`lock_a`. Lancées ensemble par `asyncio.gather`, chaque coroutine finit par "
        "tenir le verrou que l'autre attend. Le test test_bank.py borne l'exécution "
        "par `asyncio.wait_for` et échoue donc en TimeoutError.\n"
        "Attention : il ne MANQUE pas de verrou — il y en a deux, pris dans des "
        "ordres opposés. Corrige bank.py pour que les deux verrous soient toujours "
        "acquis dans le MÊME ordre par toutes les coroutines.\n"
        "Contraintes :\n"
        "  - garde les DEUX verrous (`lock_a` et `lock_b`) : ils protègent chacun un "
        "compte, ne supprime pas l'un d'eux ;\n"
        "  - garde le `await asyncio.sleep(0)` : il simule l'IO qui rend "
        "l'entrelacement possible, et c'est lui qui rend le bug reproductible ;\n"
        "  - garde `asyncio.gather` : les transferts doivent rester concurrents ;\n"
        "  - le solde final doit valoir {'a': 80, 'b': 120}.\n"
        "Ne modifie PAS test_bank.py (c'est la spec). "
        "Lance pytest à la fin pour confirmer que le test passe."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "bank.py").write_text(
            "import asyncio\n"
            "\n"
            "balances = {'a': 100, 'b': 100}\n"
            "lock_a = asyncio.Lock()\n"
            "lock_b = asyncio.Lock()\n"
            "\n"
            "\n"
            "async def transfer_a_to_b(amount):\n"
            "    # Ordre d'acquisition : a puis b.\n"
            "    async with lock_a:\n"
            "        await asyncio.sleep(0)  # IO simulée : rend l'entrelacement possible\n"
            "        async with lock_b:\n"
            "            balances['a'] -= amount\n"
            "            balances['b'] += amount\n"
            "\n"
            "\n"
            "async def transfer_b_to_a(amount):\n"
            "    # Ordre INVERSE : b puis a. C'est le bug.\n"
            "    async with lock_b:\n"
            "        await asyncio.sleep(0)\n"
            "        async with lock_a:\n"
            "            balances['b'] -= amount\n"
            "            balances['a'] += amount\n"
            "\n"
            "\n"
            "async def run_transfers():\n"
            "    balances['a'], balances['b'] = 100, 100\n"
            "    await asyncio.gather(\n"
            "        *(transfer_a_to_b(10) for _ in range(5)),\n"
            "        *(transfer_b_to_a(10) for _ in range(3)),\n"
            "    )\n"
            "    return dict(balances)\n",
            encoding="utf-8",
        )
        (workdir / "test_bank.py").write_text(
            "import asyncio\n"
            "\n"
            "from bank import run_transfers\n"
            "\n"
            "\n"
            "async def _borne():\n"
            "    # wait_for plutôt qu'un appel nu : un interblocage doit FAIRE ÉCHOUER\n"
            "    # le test, pas figer pytest indéfiniment.\n"
            "    return await asyncio.wait_for(run_transfers(), timeout=2.0)\n"
            "\n"
            "\n"
            "def test_les_transferts_aboutissent():\n"
            "    soldes = asyncio.run(_borne())\n"
            "    assert soldes == {'a': 80, 'b': 120}, f'soldes inattendus: {soldes}'\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import re
        import subprocess
        import sys

        test_path = workdir / "test_bank.py"
        if not test_path.exists():
            return False, "test_bank.py supprimé (interdit)"
        test_src = test_path.read_text(encoding="utf-8")
        for marque in ("asyncio.wait_for(run_transfers()", "{'a': 80, 'b': 120}"):
            if marque not in test_src:
                return False, "test_bank.py a été modifié (interdit)"

        src_path = workdir / "bank.py"
        if not src_path.exists():
            return False, "bank.py absent"
        src = src_path.read_text(encoding="utf-8")

        # L'IO simulée doit subsister : sans elle l'entrelacement disparaît et le
        # test passerait sans que l'ordre des verrous ait été corrigé.
        if not re.search(r"asyncio\.sleep\s*\(", src):
            return False, "asyncio.sleep supprimé : l'entrelacement a été retiré, pas corrigé"

        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"bank.py invalide (SyntaxError): {exc}"

        # Les deux verrous doivent rester : n'en garder qu'un « résout » par
        # suppression du parallélisme fin, pas par ordonnancement.
        n_locks = sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "Lock"
        )
        if n_locks < 2:
            return False, f"{n_locks} asyncio.Lock() trouvé(s), 2 attendus (verrou supprimé)"

        # La concurrence doit rester : gather appelé avec des arguments.
        if not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "gather"
            and (n.args or n.keywords)
            for n in ast.walk(tree)
        ):
            return False, "asyncio.gather(...) sans arguments : concurrence supprimée"

        # Anti-hardcode : run_transfers ne doit pas renvoyer le dict attendu en dur.
        run_fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_transfers"
            ),
            None,
        )
        if run_fn is None:
            return False, "async def run_transfers() absente"
        for ret in (n for n in ast.walk(run_fn) if isinstance(n, ast.Return)):
            val = ret.value
            if isinstance(val, ast.Dict) and all(
                isinstance(v, ast.Constant) for v in val.values
            ):
                return False, "run_transfers() renvoie un dict littéral (anti-hardcode)"
            if isinstance(val, ast.Constant):
                return False, "run_transfers() renvoie une constante (anti-hardcode)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"
        if "1 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:90]}"
        return True, "ordre d'acquisition unifié, 2 verrous conservés, soldes exacts"


@register
class CrossModuleRename(Task):
    """Expert : renommage propagé sur 3 fichiers + alias rétro-compatible.

    Tout le reste du banc tient dans un fichier. Ici la modification doit rester
    cohérente sur quatre : renommer la définition ne suffit pas, il faut
    retrouver les appelants — et laisser derrière soi un alias déprécié pour le
    code externe qu'on ne contrôle pas.

    C'est le seul endroit du banc où l'agent doit CHERCHER ce qu'il doit changer
    au lieu de le lire dans l'énoncé.
    """

    id = "expert/cross_module_rename"
    category = "expert"
    prompt = (
        "Renomme la fonction `compute_total` de calc.py en `compute_invoice_total`, "
        "et propage le changement dans TOUT le projet :\n"
        "  1. dans calc.py, la fonction s'appelle désormais `compute_invoice_total` "
        "(même comportement, même signature) ;\n"
        "  2. tous les modules qui l'appellent doivent utiliser le NOUVEAU nom — "
        "cherche-les, il y en a plus d'un ;\n"
        "  3. calc.py doit CONSERVER un `compute_total` qui fonctionne encore, pour "
        "le code externe qu'on ne contrôle pas : il délègue à "
        "`compute_invoice_total` et émet un `DeprecationWarning` "
        "(`warnings.warn(..., DeprecationWarning, stacklevel=2)`) à chaque appel. "
        "Il doit renvoyer exactement le même résultat.\n"
        "Ne modifie PAS test_billing.py (c'est la spec). "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "calc.py").write_text(
            "def compute_total(items):\n"
            '    """Somme quantité × prix unitaire, arrondie au centime."""\n'
            "    return round(sum(i['qty'] * i['unit_price'] for i in items), 2)\n",
            encoding="utf-8",
        )
        (workdir / "billing.py").write_text(
            "from calc import compute_total\n"
            "\n"
            "\n"
            "def invoice(items, tax_rate=0.2):\n"
            "    ht = compute_total(items)\n"
            "    return round(ht * (1 + tax_rate), 2)\n",
            encoding="utf-8",
        )
        (workdir / "report.py").write_text(
            "import calc\n"
            "\n"
            "\n"
            "def summary(orders):\n"
            "    return {name: calc.compute_total(items) for name, items in orders.items()}\n",
            encoding="utf-8",
        )
        (workdir / "test_billing.py").write_text(
            "import warnings\n"
            "\n"
            "import calc\n"
            "from billing import invoice\n"
            "from report import summary\n"
            "\n"
            "ITEMS = [{'qty': 2, 'unit_price': 10.0}, {'qty': 1, 'unit_price': 5.5}]\n"
            "\n"
            "\n"
            "def test_nouveau_nom():\n"
            "    assert calc.compute_invoice_total(ITEMS) == 25.5\n"
            "\n"
            "\n"
            "def test_appelants_inchanges_en_comportement():\n"
            "    assert invoice(ITEMS) == 30.6\n"
            "    assert summary({'acme': ITEMS}) == {'acme': 25.5}\n"
            "\n"
            "\n"
            "def test_ancien_nom_deprecie_mais_fonctionnel():\n"
            "    with warnings.catch_warnings(record=True) as vus:\n"
            "        warnings.simplefilter('always')\n"
            "        assert calc.compute_total(ITEMS) == 25.5\n"
            "    assert any(issubclass(w.category, DeprecationWarning) for w in vus), \\\n"
            "        'compute_total doit émettre un DeprecationWarning'\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        test_path = workdir / "test_billing.py"
        if not test_path.exists():
            return False, "test_billing.py supprimé (interdit)"
        test_src = test_path.read_text(encoding="utf-8")
        for marque in ("compute_invoice_total(ITEMS) == 25.5", "DeprecationWarning"):
            if marque not in test_src:
                return False, "test_billing.py a été modifié (interdit)"

        for nom in ("calc.py", "billing.py", "report.py"):
            if not (workdir / nom).exists():
                return False, f"{nom} supprimé (interdit)"

        # Les APPELANTS doivent porter le nouveau nom. Sans ce contrôle, l'alias
        # rétro-compatible suffirait à faire passer les tests sans que le
        # renommage ait été propagé — exactement ce que la tâche mesure.
        for nom in ("billing.py", "report.py"):
            src = (workdir / nom).read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                return False, f"{nom} invalide (SyntaxError): {exc}"
            appels = {
                n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
            }
            if "compute_total" in appels:
                return False, f"{nom} appelle encore compute_total (renommage non propagé)"
            if "compute_invoice_total" not in appels:
                return False, f"{nom} n'appelle pas compute_invoice_total"

        # L'alias doit DÉLÉGUER, pas dupliquer le calcul : une copie du corps
        # passerait les tests puis divergerait au premier changement de règle.
        calc_src = (workdir / "calc.py").read_text(encoding="utf-8")
        try:
            calc_tree = ast.parse(calc_src)
        except SyntaxError as exc:
            return False, f"calc.py invalide (SyntaxError): {exc}"
        alias = next(
            (
                n
                for n in ast.walk(calc_tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "compute_total"
            ),
            None,
        )
        if alias is None:
            return False, "compute_total supprimé de calc.py (l'alias déprécié est exigé)"
        delegue = any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == "compute_invoice_total")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "compute_invoice_total")
            )
            for n in ast.walk(alias)
        )
        if not delegue:
            return False, "compute_total ne délègue pas à compute_invoice_total (corps dupliqué)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"
        if "3 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:90]}"
        return True, "renommage propagé sur 2 appelants + alias déprécié qui délègue"


@register
class SpecBeyondTests(Task):
    """Expert : les tests visibles sont DÉJÀ verts, et la spec les dépasse.

    Toutes les autres tâches du banc partent d'un test rouge. Celle-ci part d'un
    test vert : `pytest` seul dit « tout va bien ». La spec qui fait foi est le
    docstring, et il couvre quatre cas que les tests n'exercent pas.

    Ce que ça mesure : lire la spécification plutôt que courir après le
    voyant vert. C'est le mode d'échec classique d'un agent optimisé sur
    « faire passer les tests » — et il est invisible tant qu'aucune tâche ne
    dissocie les deux.
    """

    id = "expert/spec_beyond_tests"
    category = "expert"
    prompt = (
        "Le fichier duration.py contient `parse_duration(texte)`. ATTENTION : les "
        "tests de test_duration.py PASSENT DÉJÀ — ils ne couvrent qu'une partie de "
        "la spécification. La spécification qui fait foi est le DOCSTRING de la "
        "fonction, et l'implémentation actuelle ne la respecte pas entièrement.\n"
        "Lis le docstring, repère les cas non implémentés, et complète "
        "`parse_duration` pour respecter TOUTE la spec — y compris les durées "
        "composées et les entrées invalides. Ne modifie NI le docstring NI "
        "test_duration.py. Tu peux ajouter des tests dans un AUTRE fichier."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "duration.py").write_text(
            "import re\n"
            "\n"
            "_SIMPLE = re.compile(r'^(\\d+)([smh])$')\n"
            "_UNITES = {'s': 1, 'm': 60, 'h': 3600}\n"
            "\n"
            "\n"
            "def parse_duration(texte):\n"
            '    """Convertit une durée textuelle en nombre entier de secondes.\n'
            "\n"
            "    Unités reconnues : `s` (secondes), `m` (minutes), `h` (heures).\n"
            "\n"
            "    Spécification complète :\n"
            "      1. durée simple : '90s' -> 90, '5m' -> 300, '2h' -> 7200\n"
            "      2. durée composée, unités décroissantes et sans séparateur :\n"
            "         '1h30m' -> 5400, '2h5m30s' -> 7530, '10m30s' -> 630\n"
            "      3. zéro admis : '0s' -> 0, '0m' -> 0\n"
            "      4. une unité ne peut apparaître qu'une seule fois : '1h2h'\n"
            "         est invalide\n"
            "      5. toute entrée invalide lève ValueError. Sont invalides :\n"
            "         la chaîne vide, un nombre sans unité ('45'), une unité\n"
            "         inconnue ('5d'), une valeur négative ('-5m'), une unité\n"
            "         seule ('m'), et tout texte qui n'est pas entièrement\n"
            "         consommé par le motif ('5m ' ou '5m suffixe')\n"
            "      6. le résultat est toujours un int, jamais un float\n"
            '    """\n'
            "    # Ne gère que le cas 1 : une seule unité, pas de composition,\n"
            "    # et l'invalidité renvoie None au lieu de lever.\n"
            "    m = _SIMPLE.match(texte)\n"
            "    if m is None:\n"
            "        return None\n"
            "    return int(m.group(1)) * _UNITES[m.group(2)]\n",
            encoding="utf-8",
        )
        (workdir / "test_duration.py").write_text(
            "# Ces tests passent DÉJÀ. Ils ne couvrent pas toute la spec du docstring.\n"
            "from duration import parse_duration\n"
            "\n"
            "\n"
            "def test_secondes():\n"
            "    assert parse_duration('90s') == 90\n"
            "\n"
            "\n"
            "def test_minutes():\n"
            "    assert parse_duration('5m') == 300\n"
            "\n"
            "\n"
            "def test_heures():\n"
            "    assert parse_duration('2h') == 7200\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import json
        import subprocess
        import sys

        test_path = workdir / "test_duration.py"
        if not test_path.exists():
            return False, "test_duration.py supprimé (interdit)"
        if "parse_duration('90s') == 90" not in test_path.read_text(encoding="utf-8"):
            return False, "test_duration.py a été modifié (interdit)"

        src_path = workdir / "duration.py"
        if not src_path.exists():
            return False, "duration.py absent"
        src = src_path.read_text(encoding="utf-8")
        # Le docstring est la spec : la réécrire pour coller au code serait
        # déplacer la cible plutôt qu'atteindre celle qui était posée.
        # Les repères sont pris SUR UNE SEULE LIGNE : la spec est du texte
        # enveloppé, et un repère à cheval sur deux lignes ne se retrouverait
        # jamais dans la source — la garde aurait alors rejeté toute solution,
        # y compris correcte (constaté en écrivant les tests).
        for marque in (
            "'1h30m' -> 5400",     # clause 2 : composition
            "'2h5m30s' -> 7530",   # clause 2 : trois unités
            "zéro admis",          # clause 3
            "lève ValueError",     # clause 5 : entrées invalides
            "jamais un float",     # clause 6 : type de retour
        ):
            if marque not in src:
                return False, "le docstring (la spec) a été modifié (interdit)"

        cas_ok = [
            ("90s", 90), ("5m", 300), ("2h", 7200),          # spec 1
            ("1h30m", 5400), ("2h5m30s", 7530), ("10m30s", 630),  # spec 2
            ("0s", 0), ("0m", 0),                            # spec 3
        ]
        cas_ko = ["", "45", "5d", "-5m", "m", "5m ", "5m suffixe", "1h2h"]  # spec 4 et 5

        probe = (
            "import json\n"
            "from duration import parse_duration\n"
            "echecs = []\n"
            f"for texte, attendu in {cas_ok!r}:\n"
            "    try:\n"
            "        vu = parse_duration(texte)\n"
            "    except Exception as exc:\n"
            "        echecs.append('%r: %s levée au lieu de %r'"
            " % (texte, type(exc).__name__, attendu))\n"
            "        continue\n"
            "    if vu != attendu or not isinstance(vu, int) or isinstance(vu, bool):\n"
            "        echecs.append('%r: %r attendu, %r obtenu' % (texte, attendu, vu))\n"
            f"for texte in {cas_ko!r}:\n"
            "    try:\n"
            "        vu = parse_duration(texte)\n"
            "    except ValueError:\n"
            "        continue\n"
            "    except Exception as exc:\n"
            "        echecs.append('%r: %s levée, ValueError attendue'"
            " % (texte, type(exc).__name__))\n"
            "        continue\n"
            "    echecs.append('%r: ValueError attendue, %r renvoyé' % (texte, vu))\n"
            "print(json.dumps(echecs))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"duration.py inutilisable: {tail[-1][:90] if tail else 'no output'}"
        try:
            echecs = json.loads(pr.stdout.strip().splitlines()[-1])
        except Exception:
            return False, f"sortie probe illisible: {pr.stdout.strip()[:80]}"

        if echecs:
            return False, f"{len(echecs)}/{len(cas_ok) + len(cas_ko)} cas hors spec — {echecs[0]}"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"régression sur les tests visibles: {tail[-1][:80] if tail else '?'}"
        return True, f"{len(cas_ok) + len(cas_ko)}/{len(cas_ok) + len(cas_ko)} cas de la spec OK"


@register
class StreamMemory(Task):
    """Expert : la contrainte est la mémoire crête, pas le temps.

    `hard/optimize_n_squared` et `expert/unhashable_dedup` se jugent au
    chronomètre. Ici le temps est déjà correct : c'est la crête mémoire qui est
    plafonnée, mesurée par `tracemalloc`. Matérialiser la liste coûte ~7 Mo là
    où un générateur coûte quelques kilo-octets.

    L'optimisation « rendre plus rapide » ne s'applique pas — il faut voir que
    la ressource contrainte a changé.
    """

    id = "expert/stream_memory"
    category = "expert"
    prompt = (
        "Le fichier readings.py calcule la somme des entiers d'un fichier texte "
        "(une valeur par ligne). Il fonctionne, mais `load_numbers` construit la "
        "liste ENTIÈRE en mémoire avant que `total` n'en fasse la somme : sur un "
        "gros fichier, la crête mémoire explose.\n"
        "Réécris-le pour qu'il traite le fichier EN FLUX, sans jamais matérialiser "
        "toutes les valeurs à la fois (générateur, ou somme accumulée au fil de la "
        "lecture). Contraintes :\n"
        "  - `total(path)` garde sa signature et renvoie exactement le même entier ;\n"
        "  - les lignes vides sont ignorées, comme aujourd'hui ;\n"
        "  - n'utilise NI `.read()` NI `.readlines()` NI `list(f)` : ils chargent "
        "tout le fichier ;\n"
        "  - la crête mémoire mesurée par `tracemalloc` pendant `total()` doit "
        "rester sous 2 Mo sur un fichier de 200 000 lignes.\n"
        "Ne modifie PAS test_readings.py (c'est la spec). "
        "Lance pytest à la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "readings.py").write_text(
            "def load_numbers(path):\n"
            '    """Lit toutes les valeurs du fichier et les renvoie en liste."""\n'
            "    with open(path) as f:\n"
            "        # PROBLÈME : la liste entière tient en mémoire avant la somme.\n"
            "        return [int(line) for line in f if line.strip()]\n"
            "\n"
            "\n"
            "def total(path):\n"
            '    """Somme des valeurs du fichier."""\n'
            "    return sum(load_numbers(path))\n",
            encoding="utf-8",
        )
        (workdir / "test_readings.py").write_text(
            "from readings import total\n"
            "\n"
            "\n"
            "def _ecrire(tmp_path, valeurs):\n"
            "    p = tmp_path / 'valeurs.txt'\n"
            "    p.write_text('\\n'.join(str(v) for v in valeurs) + '\\n', encoding='utf-8')\n"
            "    return str(p)\n"
            "\n"
            "\n"
            "def test_somme(tmp_path):\n"
            "    assert total(_ecrire(tmp_path, [1, 2, 3, 4])) == 10\n"
            "\n"
            "\n"
            "def test_lignes_vides_ignorees(tmp_path):\n"
            "    p = tmp_path / 'trous.txt'\n"
            "    p.write_text('1\\n\\n2\\n\\n\\n3\\n', encoding='utf-8')\n"
            "    assert total(str(p)) == 6\n"
            "\n"
            "\n"
            "def test_fichier_vide(tmp_path):\n"
            "    p = tmp_path / 'vide.txt'\n"
            "    p.write_text('', encoding='utf-8')\n"
            "    assert total(str(p)) == 0\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import json
        import re
        import subprocess
        import sys

        test_path = workdir / "test_readings.py"
        if not test_path.exists():
            return False, "test_readings.py supprimé (interdit)"
        if "test_lignes_vides_ignorees" not in test_path.read_text(encoding="utf-8"):
            return False, "test_readings.py a été modifié (interdit)"

        src_path = workdir / "readings.py"
        if not src_path.exists():
            return False, "readings.py absent"
        src = src_path.read_text(encoding="utf-8")
        for interdit, motif in (
            (".read()", r"\.read\s*\(\s*\)"),
            (".readlines()", r"\.readlines\s*\("),
            ("list(f)", r"\blist\s*\(\s*f\w*\s*\)"),
        ):
            if re.search(motif, src):
                return False, f"{interdit} interdit : charge tout le fichier en mémoire"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"

        # Porte mémoire. Les valeurs sont à 7 chiffres et toutes distinctes : hors
        # de la plage de petits entiers mise en cache par CPython, donc une liste
        # matérialisée alloue vraiment (~7 Mo), là où un flux reste sous 100 Ko.
        probe = (
            "import json, tracemalloc\n"
            "from readings import total\n"
            "N = 200000\n"
            "attendu = 0\n"
            "with open('gros.txt', 'w', encoding='utf-8') as f:\n"
            "    for i in range(N):\n"
            "        v = 1000000 + i\n"
            "        attendu += v\n"
            "        f.write('%d\\n' % v)\n"
            "tracemalloc.start()\n"
            "vu = total('gros.txt')\n"
            "_, crete = tracemalloc.get_traced_memory()\n"
            "tracemalloc.stop()\n"
            "print(json.dumps({'vu': vu, 'attendu': attendu, 'crete': crete}))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=300, cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return False, f"probe KO: {tail[-1][:90] if tail else 'no output'}"
        try:
            d = json.loads(pr.stdout.strip().splitlines()[-1])
        except Exception:
            return False, f"sortie probe illisible: {pr.stdout.strip()[:80]}"

        if d["vu"] != d["attendu"]:
            return False, f"somme fausse sur 200k lignes: {d['attendu']} attendu, {d['vu']} vu"

        plafond = 2 * 1024 * 1024
        crete_mo = d["crete"] / (1024 * 1024)
        if d["crete"] >= plafond:
            return False, f"crête mémoire {crete_mo:.1f} Mo >= 2.0 Mo : toujours matérialisé"
        return True, f"traitement en flux, crête {crete_mo:.3f} Mo < 2.0 Mo"
