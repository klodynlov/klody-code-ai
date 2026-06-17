"""Tâches hard — multi-étapes, débogage, refactor structurel.

Discriminantes et 100 % stdlib : aucune dépendance tierce (fastapi/httpx/
pydantic/pytest-asyncio) requise pour valider. La fixture brute échoue, seule
une vraie solution passe (gardes AST + exécution/pytest, anti-hardcode).
"""

from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


@register
class FixAsyncBug(Task):
    """Hard task : race condition « lost update » en asyncio pur.

    Counter.increment() lit self.total, fait un `await` (yield au scheduler),
    puis ecrit current + 1. Entre la lecture et l'ecriture, les autres taches
    lancees par asyncio.gather s'intercalent et lisent la meme valeur => les
    mises a jour se perdent et le total final est faux. Le test exige 200 et
    echoue. Fix attendu : un asyncio.Lock (ou supprimer l'interleave non sur).
    Stdlib asyncio uniquement, AUCUNE dependance pytest-asyncio (le test
    appelle asyncio.run dans une fonction de test ordinaire).
    """

    id = "hard/fix_async_bug"
    category = "hard"
    prompt = (
        "Le fichier counter.py contient une race condition. La methode "
        "Counter.increment() lit self.total, fait un `await asyncio.sleep(0)`, "
        "puis ecrit current + 1 : entre la lecture et l'ecriture, les autres "
        "taches concurrentes lancees par asyncio.gather s'intercalent et "
        "ecrasent la valeur (lost update). Le test test_counter.py lance 200 "
        "increments concurrents et exige un total de 200, mais il echoue. "
        "Corrige counter.py pour rendre l'increment atomique (utilise un "
        "asyncio.Lock, ou supprime l'interleave dangereux) en restant en "
        "asyncio standard, SANS aucune dependance tierce (pas de "
        "pytest-asyncio). Ne modifie PAS test_counter.py (c'est la spec). "
        "Lance pytest a la fin pour confirmer que le test passe."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "counter.py").write_text(
            "import asyncio\n"
            "\n"
            "\n"
            "class Counter:\n"
            "    def __init__(self) -> None:\n"
            "        self.total = 0\n"
            "\n"
            "    async def increment(self) -> None:\n"
            "        # BUG : lecture, puis await (yield au scheduler), puis ecriture.\n"
            "        # Entre le read et le write, une autre tache modifie self.total\n"
            "        # => mise a jour perdue (lost update). Aucun verrou.\n"
            "        current = self.total\n"
            "        await asyncio.sleep(0)\n"
            "        self.total = current + 1\n"
            "\n"
            "\n"
            "async def run(n: int) -> int:\n"
            "    counter = Counter()\n"
            "    await asyncio.gather(*(counter.increment() for _ in range(n)))\n"
            "    return counter.total\n",
            encoding="utf-8",
        )
        (workdir / "test_counter.py").write_text(
            "import asyncio\n"
            "\n"
            "from counter import run\n"
            "\n"
            "\n"
            "def test_no_lost_updates():\n"
            "    # 200 increments concurrents => total DOIT valoir 200.\n"
            "    total = asyncio.run(run(200))\n"
            '    assert total == 200, f"lost-update race: attendu 200, obtenu {total}"\n',
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import subprocess
        import sys

        # Anti-triche : la spec du test ne doit pas avoir bouge.
        test_path = workdir / "test_counter.py"
        if not test_path.exists():
            return False, "test_counter.py supprime (interdit)"
        test_src = test_path.read_text(encoding="utf-8")
        if "asyncio.run(run(200))" not in test_src or "total == 200" not in test_src:
            return False, "test_counter.py a ete modifie (interdit)"

        # Anti-triche : pas de pytest-asyncio (doit rester stdlib asyncio).
        counter_path = workdir / "counter.py"
        if not counter_path.exists():
            return False, "counter.py absent"
        counter_src = counter_path.read_text(encoding="utf-8")
        if "pytest_asyncio" in counter_src or "pytest-asyncio" in counter_src:
            return False, "dependance pytest-asyncio interdite"

        # Anti-triche AST : la concurrence (asyncio.gather) doit subsister et
        # run() ne doit pas hardcoder le resultat (constante litterale) ni
        # renvoyer le parametre brut (ex. `return n`) sans calculer le total.
        try:
            tree = ast.parse(counter_src)
        except SyntaxError as exc:
            return False, f"counter.py invalide (SyntaxError): {exc}"
        has_gather = any(
            isinstance(node, ast.Attribute) and node.attr == "gather" for node in ast.walk(tree)
        )
        if not has_gather:
            return False, "asyncio.gather supprime (la concurrence est requise)"
        run_fn = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "run"
            ),
            None,
        )
        if run_fn is None:
            return False, "fonction async run() absente"
        params = {a.arg for a in run_fn.args.args}
        params |= {a.arg for a in run_fn.args.posonlyargs}
        params |= {a.arg for a in run_fn.args.kwonlyargs}
        returns = [n for n in ast.walk(run_fn) if isinstance(n, ast.Return)]
        if not returns:
            return False, "run() ne renvoie rien"
        for ret in returns:
            val = ret.value
            if val is None or isinstance(val, ast.Constant):
                return False, "run() renvoie une constante (anti-hardcode)"
            if isinstance(val, ast.Name) and val.id in params:
                return False, "run() renvoie un parametre brut (anti-hardcode)"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_path),
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:90] if tail else 'no output'}"
        if "1 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:90]}"
        return True, "race corrigee, total atomique == 200 (1/1 test passe)"


@register
class OptimizeNSquared(Task):
    """Hard task : optimisation algorithmique O(n^2) -> O(n).

    Fixture : `has_duplicate(items)` implementee en double boucle imbriquee
    (O(n^2)) — CORRECTE mais lente. L'agent doit la reimplementer en O(n)
    (set de vus) en conservant exactement le meme comportement.

    Validation discriminante :
      1. La spec (test_dups.py) doit toujours passer (correction preservee).
      2. Une porte de perf : sur N=30000 elements SANS doublon (pire cas,
         scan complet), la version O(n^2) depasse largement le budget
         (~5 s mesure) tandis qu'une version O(n) est sous la milliseconde.
         Budget genereux de 1.5 s : O(n^2) echoue net, O(n) passe net.
      3. Le probe verifie aussi le comportement sur le grand N (False sans
         doublon, True avec un doublon ajoute) — un hack "return False/True
         constant" rapide est rejete par pytest ET par le probe.
    """

    id = "hard/optimize_n_squared"
    category = "hard"
    prompt = (
        "Le fichier dups.py contient une fonction `has_duplicate(items)` qui "
        "indique si une liste contient au moins un doublon. Elle est CORRECTE "
        "mais utilise une double boucle imbriquee en O(n^2), donc beaucoup trop "
        "lente sur de grandes listes. Reecris-la en O(n) (ou O(n log n)) en "
        "conservant EXACTEMENT le meme comportement (memes retours True/False, "
        "y compris liste vide). Ne modifie PAS test_dups.py (c'est la spec). "
        "Lance pytest a la fin pour confirmer que les tests passent."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "dups.py").write_text(
            "def has_duplicate(items):\n"
            '    """Retourne True si la liste contient au moins un doublon, False sinon."""\n'
            "    n = len(items)\n"
            "    for i in range(n):\n"
            "        for j in range(i + 1, n):\n"
            "            if items[i] == items[j]:\n"
            "                return True\n"
            "    return False\n",
            encoding="utf-8",
        )
        (workdir / "test_dups.py").write_text(
            "from dups import has_duplicate\n"
            "\n"
            "def test_with_duplicate():\n"
            "    assert has_duplicate([1, 2, 3, 2, 5]) is True\n"
            "\n"
            "def test_no_duplicate():\n"
            "    assert has_duplicate([1, 2, 3, 4, 5]) is False\n"
            "\n"
            "def test_empty():\n"
            "    assert has_duplicate([]) is False\n"
            "\n"
            "def test_single():\n"
            "    assert has_duplicate([42]) is False\n"
            "\n"
            "def test_strings():\n"
            '    assert has_duplicate(["a", "b", "a"]) is True\n'
            '    assert has_duplicate(["a", "b", "c"]) is False\n',
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import json
        import subprocess
        import sys

        # Anti-triche : la spec ne doit pas avoir ete denaturee.
        test_src = (workdir / "test_dups.py").read_text(encoding="utf-8")
        if "has_duplicate([1, 2, 3, 2, 5]) is True" not in test_src:
            return False, "test_dups.py modifie (interdit)"
        if "has_duplicate([1, 2, 3, 4, 5]) is False" not in test_src:
            return False, "test_dups.py modifie (interdit)"

        # 1) Correction : la spec doit passer.
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(workdir / "test_dups.py"), "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:80] if tail else 'no output'}"

        # 2) Comportement sur grand N + porte de perf (pire cas = aucun doublon).
        probe = (
            "import time, json\n"
            "from dups import has_duplicate\n"
            "N = 30000\n"
            "no_dup = list(range(N))\n"
            "with_dup = list(range(N)) + [0]\n"
            "assert has_duplicate(no_dup) is False, 'comportement: faux positif sur grand N'\n"
            "assert has_duplicate(with_dup) is True, 'comportement: doublon non detecte sur grand N'\n"
            "t = time.perf_counter()\n"
            "has_duplicate(list(range(N)))\n"
            "el = time.perf_counter() - t\n"
            "print(json.dumps({'elapsed': el}))\n"
        )
        pr = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workdir,
        )
        if pr.returncode != 0:
            tail = (pr.stdout + pr.stderr).strip().splitlines()
            return (
                False,
                f"probe KO (comportement grand N): {tail[-1][:80] if tail else 'no output'}",
            )
        try:
            elapsed = json.loads(pr.stdout.strip().splitlines()[-1])["elapsed"]
        except Exception:
            return False, f"sortie probe illisible: {pr.stdout.strip()[:80]}"

        budget = 1.5
        if elapsed >= budget:
            return False, f"trop lent ({elapsed:.3f}s >= {budget}s) : algo O(n^2) non optimise"
        return True, f"correct + lineaire ({elapsed:.3f}s < {budget}s)"


@register
class MigrateSyncToAsync(Task):
    """Hard task : convertir un module synchrone en asyncio, SANS lib tierce.

    La fixture `service.py` simule une IO lente avec `time.sleep` (stub stdlib,
    PAS de httpx, PAS de reseau). Un driver appelle les fonctions sequentiellement.
    L'agent doit :
      - passer fetch / fetch_all / aggregate / main en `async def` ;
      - remplacer `time.sleep` par `await asyncio.sleep` ;
      - lancer les fetch EN CONCURRENCE via `asyncio.gather` ;
      - exposer un point d'entree lancable par `asyncio.run`.

    Validation 100% stdlib et ANTI-TRICHE : ast (async def + await + gather
    appele AVEC arguments) + execution reelle via asyncio.run (resultat == 100)
    + mesure de concurrence robuste. Le validate monkeypatche `asyncio.sleep`
    pour IMPOSER son propre delai deterministe (independant de la valeur ecrite
    par l'agent), compte les appels (>=4) et exige un temps mur nettement
    inferieur a la somme sequentielle -> impossible de tricher en supprimant
    les sleeps ou en codant le resultat en dur.
    """

    id = "hard/migrate_sync_to_async"
    category = "hard"
    prompt = (
        "Le module `service.py` est synchrone : ses fonctions simulent une IO lente "
        "avec `time.sleep` et sont appelees sequentiellement, ce qui est lent. "
        "Convertis-le en asyncio en utilisant UNIQUEMENT la bibliotheque standard "
        "(N'UTILISE PAS httpx ni aucune lib tierce : remplace l'IO par "
        "`await asyncio.sleep`). Concretement :\n"
        "  1. passe `fetch`, `fetch_all`, `aggregate` et `main` en `async def` ;\n"
        "  2. remplace chaque `time.sleep(...)` par `await asyncio.sleep(...)` "
        "(garde l'IO simulee : c'est elle qu'on parallelise) ;\n"
        "  3. dans `fetch_all`, lance tous les `fetch` EN CONCURRENCE avec "
        "`asyncio.gather(...)` (et non un par un dans une boucle) ;\n"
        "  4. garde le meme resultat agrege ((1+2+3+4)*10 == 100) ;\n"
        "  5. dans le bloc `if __name__ == '__main__'`, lance le tout via "
        "`asyncio.run(main())`.\n"
        "Ne modifie aucun autre fichier."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "service.py").write_text(
            "import time\n"
            "\n"
            "# Stub stdlib qui simule une IO lente (PAS de httpx, PAS de reseau).\n"
            "def fetch(item_id):\n"
            "    time.sleep(0.3)  # IO simulee\n"
            "    return item_id * 10\n"
            "\n"
            "def fetch_all(item_ids):\n"
            "    results = []\n"
            "    for item_id in item_ids:\n"
            "        results.append(fetch(item_id))\n"
            "    return results\n"
            "\n"
            "def aggregate(item_ids):\n"
            "    return sum(fetch_all(item_ids))\n"
            "\n"
            "def main():\n"
            "    return aggregate([1, 2, 3, 4])\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print(main())\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast
        import re
        import subprocess
        import sys

        path = workdir / "service.py"
        if not path.exists():
            return False, "service.py manquant"
        src = path.read_text(encoding="utf-8")

        # Anti-triche : pas de lib tierce httpx, meme si le brief d'origine la citait.
        if re.search(r"\bimport\s+httpx\b", src) or re.search(r"\bfrom\s+httpx\b", src):
            return False, "httpx interdit (stdlib uniquement)"
        # time.sleep bloquant interdit : doit etre remplace par asyncio.sleep.
        if re.search(r"\btime\.sleep\b", src):
            return False, "time.sleep restant : IO toujours bloquante"
        # Anti-triche : l'IO simulee non bloquante DOIT subsister, sinon il n'y a
        # plus rien a paralleliser (et on pourrait coder le resultat en dur).
        if not re.search(r"asyncio\.sleep\s*\(", src):
            return False, "asyncio.sleep absent : l'IO simulee a ete supprimee"

        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"service.py invalide: {exc}"

        # 1. Les 4 fonctions doivent etre async def.
        async_funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        for needed in ("fetch", "fetch_all", "aggregate", "main"):
            if needed not in async_funcs:
                return False, f"`{needed}` doit etre `async def`"

        # 2. Au moins un await.
        if not any(isinstance(n, ast.Await) for n in ast.walk(tree)):
            return False, "aucun `await` trouve"

        # 3. asyncio.gather APPELE AVEC au moins un argument (concurrence reelle ;
        #    un `gather()` vide ne sert qu'a tromper l'ast et est rejete).
        uses_gather = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "gather"
            and len(n.args) >= 1
            for n in ast.walk(tree)
        )
        if not uses_gather:
            return False, "asyncio.gather(...) non appele avec arguments (pas de concurrence)"

        # 4. Execution reelle + mesure de concurrence ROBUSTE.
        #    On monkeypatche asyncio.sleep pour IMPOSER notre propre delai
        #    deterministe (DELAY) quelle que soit la valeur ecrite par l'agent :
        #    sleep(0) ne permet donc pas de contourner la mesure. On compte les
        #    appels (>=4 = 1 par fetch) et la somme des delais, puis on exige un
        #    temps mur nettement inferieur a la somme sequentielle -> preuve de
        #    concurrence reelle, et resultat agrege == 100 (vraiment calcule).
        runner = (
            "import asyncio, time, importlib.util\n"
            "DELAY = 0.25  # delai impose a chaque asyncio.sleep, deterministe\n"
            "_orig_sleep = asyncio.sleep\n"
            "_calls = {'n': 0, 'sum': 0.0}\n"
            "async def _instr_sleep(delay, *a, **k):\n"
            "    _calls['n'] += 1\n"
            "    _calls['sum'] += DELAY\n"
            "    return await _orig_sleep(DELAY)\n"
            "asyncio.sleep = _instr_sleep\n"
            "spec = importlib.util.spec_from_file_location('svc', " + repr(str(path)) + ")\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "t0 = time.perf_counter()\n"
            "res = asyncio.run(mod.main())\n"
            "dt = time.perf_counter() - t0\n"
            "print('RESULT', res)\n"
            "print('SLEEPS', _calls['n'])\n"
            "print('SUMSLEEP', round(_calls['sum'], 3))\n"
            "print('ELAPSED', round(dt, 3))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", runner],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"execution KO: {tail[-1][:90] if tail else 'no output'}"

        out = proc.stdout
        m_res = re.search(r"RESULT\s+(-?\d+)", out)
        if not m_res:
            return False, f"resultat illisible: {out.strip()[:80]}"
        if int(m_res.group(1)) != 100:  # (1+2+3+4)*10
            return False, f"resultat incorrect: attendu 100, vu {m_res.group(1)}"

        m_sleeps = re.search(r"SLEEPS\s+(\d+)", out)
        m_sum = re.search(r"SUMSLEEP\s+([\d.]+)", out)
        m_dt = re.search(r"ELAPSED\s+([\d.]+)", out)
        if not (m_sleeps and m_sum and m_dt):
            return False, "instrumentation illisible"

        n_sleeps = int(m_sleeps.group(1))
        sum_sleep = float(m_sum.group(1))
        elapsed = float(m_dt.group(1))

        # Au moins 4 appels a asyncio.sleep -> les 4 fetch ont reellement attendu.
        if n_sleeps < 4:
            return False, f"seulement {n_sleeps} asyncio.sleep (attendu >=4 : 1 par fetch)"

        # Concurrence reelle : temps mur nettement < somme des delais sequentiels.
        # Sequentiel = sum_sleep (~n*DELAY) ; concurrent ~ DELAY. Marge large : < 60%.
        if elapsed >= sum_sleep * 0.6:
            return False, (
                f"pas concurrent: {elapsed}s >= {round(sum_sleep * 0.6, 3)}s "
                f"(somme sequentielle={sum_sleep}s)"
            )

        return True, (
            f"async + gather OK, res=100, {n_sleeps} sleeps, "
            f"{elapsed}s << {sum_sleep}s (concurrent)"
        )


@register
class ApiEndpointFull(Task):
    """Hard task : ajouter un endpoint FastAPI COMPLET (modele Pydantic + route + test).

    La validation N'IMPORTE JAMAIS fastapi ni pydantic (souvent absents du venv
    du bench). Tout est verifie par analyse statique avec le module `ast` :
      (a) une classe heritant de BaseModel avec >= 2 champs annotes,
      (b) un decorateur de route (@app.get/@app.post/.../@router.*),
      (c) un handler dont un parametre OU le type de retour reference le modele,
      (d) une fonction test_ presente dans un fichier de test.
    """

    id = "hard/api_endpoint_full"
    category = "hard"
    prompt = (
        "Le fichier api.py contient une app FastAPI deja creee (`app = FastAPI()`) "
        "et un TODO. Complete-le :\n"
        "1) Definis un modele Pydantic nomme `Item` qui herite de `BaseModel` et "
        "possede AU MOINS deux champs annotes (par exemple `name: str` et "
        "`price: float`).\n"
        '2) Ajoute une route POST `/items` decoree avec `@app.post("/items")`, '
        "dont le handler prend un parametre annote `item: Item` et renvoie un `Item` "
        "(annotation de retour `-> Item`).\n"
        "3) Dans test_api.py, ecris une fonction de test (nom commencant par "
        "`test_`) qui instancie `Item` et appelle le handler pour verifier le "
        "comportement.\n"
        "Garde les imports necessaires (FastAPI deja importe ; ajoute "
        "`from pydantic import BaseModel`)."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "api.py").write_text(
            "from fastapi import FastAPI\n"
            "\n"
            "app = FastAPI()\n"
            "\n"
            "# TODO: definir un modele Pydantic `Item` (heritant de BaseModel) avec\n"
            "# au moins deux champs annotes (ex. name: str, price: float), puis\n"
            "# ajouter une route POST `/items` dont le handler prend un `item: Item`\n"
            "# en parametre et renvoie un `Item`.\n",
            encoding="utf-8",
        )
        (workdir / "test_api.py").write_text(
            "# TODO: ecrire une fonction de test (nom commencant par test_) qui\n"
            "# instancie le modele Item et verifie le handler de la route /items.\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        import ast

        api_path = workdir / "api.py"
        if not api_path.exists():
            return False, "api.py manquant"
        src = api_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"api.py ne parse pas: {exc.msg}"

        # (a) classe heritant de BaseModel (Name ou Attribute) avec >= 2 champs annotes
        model_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: set[str] = set()
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.add(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.add(base.attr)
            if "BaseModel" not in base_names:
                continue
            annotated = [
                stmt
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
            if len(annotated) >= 2:
                model_names.add(node.name)
        if not model_names:
            return False, "aucune classe BaseModel avec >=2 champs annotes"

        # (b) decorateur de route @app.<methode>/@router.<methode> (avec ou sans appel)
        http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
        routed_funcs: list = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in http_methods
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"app", "router"}
                ):
                    routed_funcs.append(node)
                    break
        if not routed_funcs:
            return False, "aucune route @app.<methode>/@router.<methode> trouvee"

        # (c) un handler reference le modele en parametre OU en type de retour
        def _refs_model(func) -> bool:
            anns = [a.annotation for a in func.args.args if a.annotation is not None]
            anns += [a.annotation for a in func.args.kwonlyargs if a.annotation is not None]
            if func.returns is not None:
                anns.append(func.returns)
            for ann in anns:
                for sub in ast.walk(ann):
                    if isinstance(sub, ast.Name) and sub.id in model_names:
                        return True
                    if isinstance(sub, ast.Attribute) and sub.attr in model_names:
                        return True
            return False

        if not any(_refs_model(f) for f in routed_funcs):
            return False, "aucun handler de route ne reference le modele Pydantic"

        # (d) une fonction test_ dans un fichier de test (test_*.py ou *_test.py)
        test_files = list(workdir.glob("test_*.py")) + list(workdir.glob("*_test.py"))
        for tf in test_files:
            t_src = tf.read_text(encoding="utf-8")
            try:
                t_tree = ast.parse(t_src)
            except SyntaxError:
                continue
            for node in ast.walk(t_tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    return True, "modele Pydantic + route + handler + test OK"
        return False, "aucune fonction test_ trouvee dans un fichier de test"


@register
class DebugTestSuite(Task):
    """Hard task : 3 tests échouent pour 3 raisons distinctes.

    Démontre l'impact du Best-of-N (Roadmap v2 #7) — sur une tâche hard,
    la 1ère décision (par où commencer ? quelle stratégie ?) est critique.

    Bugs cachés dans `calculator.py` :
      1. `divide(a, b)` — pas de vérif b == 0 → ZeroDivisionError
      2. `square(x)` — retourne x + 2 au lieu de x * x
      3. `is_positive(x)` — retourne True pour x == 0 (devrait être strict)
    """

    id = "hard/debug_test_suite"
    category = "hard"
    prompt = (
        "3 tests dans test_calculator.py échouent pour 3 raisons différentes. "
        "Lis le code, identifie chaque bug, corrige calculator.py SANS toucher "
        "aux tests (qui constituent la spec). Lance pytest à la fin pour confirmer "
        "que les 3 tests passent."
    )

    def setup(self, workdir):
        (workdir / "calculator.py").write_text(
            "def divide(a, b):\n"
            "    # BUG 1 : aucune vérif sur b == 0\n"
            "    return a / b\n"
            "\n"
            "def square(x):\n"
            "    # BUG 2 : opérateur incorrect\n"
            "    return x + 2\n"
            "\n"
            "def is_positive(x):\n"
            "    # BUG 3 : compare à 0 inclus au lieu de strict\n"
            "    return x >= 0\n",
            encoding="utf-8",
        )
        (workdir / "test_calculator.py").write_text(
            "import pytest\n"
            "from calculator import divide, square, is_positive\n"
            "\n"
            "def test_divide_normal():\n"
            "    assert divide(10, 2) == 5\n"
            "\n"
            "def test_divide_by_zero_raises():\n"
            "    with pytest.raises((ZeroDivisionError, ValueError)):\n"
            "        divide(5, 0)\n"
            "\n"
            "def test_square_positives():\n"
            "    assert square(4) == 16\n"
            "    assert square(7) == 49\n"
            "\n"
            "def test_is_positive_zero_is_false():\n"
            "    assert is_positive(0) is False\n"
            "    assert is_positive(-1) is False\n"
            "    assert is_positive(3) is True\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import subprocess
        import sys

        # Tests interdits de modif (test_calculator.py garde sa spec)
        test_src = (workdir / "test_calculator.py").read_text(encoding="utf-8")
        if "square(4) == 16" not in test_src or "is_positive(0) is False" not in test_src:
            return False, "test_calculator.py a été modifié (interdit)"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(workdir / "test_calculator.py"),
                "-q",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:80] if tail else 'no output'}"
        if "4 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:80]}"
        return True, "4/4 tests passent (3 bugs fixés)"
