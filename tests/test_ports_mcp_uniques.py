"""Aucun port par défaut partagé entre deux serveurs MCP du dépôt.

Vécu le 2026-09-30 : `klody_mcp/memory_server.py` (#224, 2026-08-16) a pris
:8095 par défaut, déjà tenu par Blender Lab (`klody_mcp/blender_server.py`,
agent `com.klody.blender-mcp`, consommé via `KLODY_MCP_SERVERS`). L'agent
`com.klody.memory-mcp` n'a donc JAMAIS été chargé — et le charger aurait mis
deux démons launchd en concurrence pour le même bind : boucle KeepAlive pour le
perdant, ou vol du port à Blender selon l'ordre de démarrage au boot. Rien ne
le disait : `test_launchagents_couvrent_les_mcp.py` compare des NOMS de
fichiers, pas des ports.

Le même inventaire a trouvé deux collisions latentes, muettes parce que les
deux serveurs tournaient en stdio : `dreamx_server` en HTTP (:8091 = vlc) et
`samplebrain_server` en HTTP (:8094 = ableton, arrivé après lui).

Jumeau de la collision #41 (`KLODY_MCP_PORT` = `MLX_CODE_PORT` = 8083), qui
avait empêché le serveur MCP propre de Klody de démarrer.

L'inventaire est lu dans le CODE — arbre syntaxique Python, affectations shell,
lignes `VAR=N` de `.env.example` —, jamais dans la prose : un commentaire ou
une docstring qui cite un port n'en réserve aucun, et un test qui les lirait
pousserait à supprimer les explications pour le faire taire (piège déjà vécu,
CLAUDE.md « Un test qui scanne le SOURCE confond la prose et la sortie »).
"""
from __future__ import annotations

import ast
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Une variable de port MCP : `MEMORY_MCP_PORT`, `KLODY_MCP_PORT`… et `MCP_PORT`
# (serveur MCP LibraryBrain, `klody_mcp/server.py`).
_VAR_PORT_MCP = re.compile(r"(?:\w+_)?MCP_PORT")
# `PORT="${MEMORY_MCP_PORT:-8100}"` dans un lanceur.
_PORT_SHELL = re.compile(r'^\s*PORT="\$\{(\w+):-(\d+)\}"', re.MULTILINE)
# `exec python -m klody_mcp.memory_server` dans un lanceur.
_DELEGATION = re.compile(r"^[^#\n]*python\S*\s+-m\s+klody_mcp\.(\w+)", re.MULTILINE)
# `MEMORY_MCP_PORT=8100` dans `.env.example` (ligne non commentée).
_PORT_ENV = re.compile(r"^((?:\w+_)?MCP_PORT)=(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Defaut:
    """Un port par défaut lu dans le code.

    `cle` identifie le SERVICE : le nom de la variable d'environnement quand le
    port en dépend (un même service la lit dans le module, le lanceur et
    `.env.example`), sinon le module qui fige le port en constante.
    """

    cle: str
    port: int
    source: str


# --------------------------------------------------------------------------- #
# Inventaire                                                                  #
# --------------------------------------------------------------------------- #


def _nom_appele(noeud: ast.expr) -> str:
    """`os.environ.get` -> "os.environ.get", `getenv` -> "getenv"."""
    if isinstance(noeud, ast.Name):
        return noeud.id
    if isinstance(noeud, ast.Attribute):
        return f"{_nom_appele(noeud.value)}.{noeud.attr}"
    return ""


def _entier(noeud: ast.expr | None) -> int | None:
    """Littéral `8100` ou `"8100"` -> 8100 ; toute expression -> None."""
    if isinstance(noeud, ast.Constant):
        if isinstance(noeud.value, int) and not isinstance(noeud.value, bool):
            return noeud.value
        if isinstance(noeud.value, str) and noeud.value.isdigit():
            return int(noeud.value)
    return None


def _defauts_python(fichier: Path, racine: Path) -> list[Defaut]:
    """Ports par défaut d'un module : `getenv("X_MCP_PORT", N)`, constante
    `PORT = N` au niveau module, `port=N` littéral passé à un appel."""
    rel = fichier.relative_to(racine).as_posix()
    arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=rel)
    trouves: list[Defaut] = []

    for noeud in arbre.body:
        cible = None
        if isinstance(noeud, ast.Assign) and len(noeud.targets) == 1:
            cible = noeud.targets[0]
        elif isinstance(noeud, ast.AnnAssign):
            cible = noeud.target
        if isinstance(cible, ast.Name) and cible.id == "PORT":
            port = _entier(noeud.value)
            if port is not None:
                trouves.append(Defaut(rel, port, f"{rel}:{noeud.lineno}"))

    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        appel = _nom_appele(noeud.func)
        if appel.endswith(("getenv", "environ.get")) and len(noeud.args) >= 2:
            nom, defaut = noeud.args[0], noeud.args[1]
            if (
                isinstance(nom, ast.Constant)
                and isinstance(nom.value, str)
                and _VAR_PORT_MCP.fullmatch(nom.value)
            ):
                port = _entier(defaut)
                if port is not None:
                    trouves.append(Defaut(nom.value, port, f"{rel}:{noeud.lineno}"))
        for mot in noeud.keywords:
            if mot.arg == "port":
                port = _entier(mot.value)
                if port is not None:
                    trouves.append(Defaut(rel, port, f"{rel}:{noeud.lineno}"))
    return trouves


def _defauts_lanceur(lanceur: Path, racine: Path) -> list[Defaut]:
    rel = lanceur.relative_to(racine).as_posix()
    texte = lanceur.read_text(encoding="utf-8")
    return [
        Defaut(var, int(port), f"{rel}:{texte.count(chr(10), 0, m.start()) + 1}")
        for m in _PORT_SHELL.finditer(texte)
        for var, port in [m.groups()]
    ]


def _defauts_env_exemple(racine: Path) -> list[Defaut]:
    fichier = racine / ".env.example"
    if not fichier.is_file():
        return []
    texte = fichier.read_text(encoding="utf-8")
    return [
        Defaut(m.group(1), int(m.group(2)),
               f".env.example:{texte.count(chr(10), 0, m.start()) + 1}")
        for m in _PORT_ENV.finditer(texte)
    ]


def modules_mcp(racine: Path) -> list[Path]:
    return sorted((racine / "klody_mcp").glob("*.py"))


def lanceurs_mcp(racine: Path) -> list[Path]:
    return sorted((racine / "scripts").glob("start-*-mcp.sh"))


def inventaire(racine: Path) -> list[Defaut]:
    """Tous les ports MCP par défaut déclarés dans le code sous `racine`."""
    defauts: list[Defaut] = []
    for module in [*modules_mcp(racine), racine / "config.py"]:
        if module.is_file():
            defauts += _defauts_python(module, racine)
    for lanceur in lanceurs_mcp(racine):
        defauts += _defauts_lanceur(lanceur, racine)
    defauts += _defauts_env_exemple(racine)
    return defauts


def collisions(defauts: list[Defaut]) -> dict[int, dict[str, list[str]]]:
    """{port: {service: [sources]}} pour tout port revendiqué par ≥ 2 services."""
    par_port: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d in defauts:
        par_port[d.port][d.cle].append(d.source)
    return {p: dict(s) for p, s in sorted(par_port.items()) if len(s) > 1}


def divergences(defauts: list[Defaut]) -> dict[str, dict[int, list[str]]]:
    """{service: {port: [sources]}} pour tout service qui a ≥ 2 défauts."""
    par_cle: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d in defauts:
        par_cle[d.cle][d.port].append(d.source)
    return {c: dict(p) for c, p in sorted(par_cle.items()) if len(p) > 1}


def _rapport(titre: str, trouve: dict) -> str:
    lignes = [titre]
    for cle, detail in trouve.items():
        lignes.append(f"  {cle} :")
        for sous_cle, sources in detail.items():
            lignes.append(f"    {sous_cle} ← {', '.join(sources)}")
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# L'instrument voit tout ce qu'il doit juger                                  #
# --------------------------------------------------------------------------- #


def _serveur_http(module: Path) -> bool:
    """Le module sert-il en HTTP ? (`mcp.run(transport="http"…)`)."""
    arbre = ast.parse(module.read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call):
            for mot in noeud.keywords:
                if (
                    mot.arg == "transport"
                    and isinstance(mot.value, ast.Constant)
                    and mot.value.value in ("http", "streamable-http", "sse")
                ):
                    return True
    return False


def test_chaque_lanceur_a_un_port_inventorie():
    """Un lanceur dont le port échappe à l'inventaire est un lanceur que la
    règle ne juge pas — il pourrait prendre n'importe quel port en silence.
    Soit le lanceur fixe son défaut (`PORT="${X_MCP_PORT:-N}"`), soit il
    délègue à un module (`python -m klody_mcp.x`) qui le fixe."""
    defauts = inventaire(REPO)
    par_source: dict[str, set[int]] = defaultdict(set)
    for d in defauts:
        par_source[d.source.rsplit(":", 1)[0]].add(d.port)

    lanceurs = lanceurs_mcp(REPO)
    assert len(lanceurs) >= 10, "l'inventaire ne voit presque aucun lanceur"
    aveugles = []
    for lanceur in lanceurs:
        rel = lanceur.relative_to(REPO).as_posix()
        if par_source.get(rel):
            continue
        texte = lanceur.read_text(encoding="utf-8")
        delegue = _DELEGATION.search(texte)
        module = f"klody_mcp/{delegue.group(1)}.py" if delegue else None
        if module and par_source.get(module):
            continue
        aveugles.append(f"{rel} (délègue à {module})" if module else rel)
    assert not aveugles, (
        "lanceurs MCP dont l'inventaire ne lit aucun port par défaut :\n  "
        + "\n  ".join(aveugles)
    )


def test_chaque_serveur_http_a_un_port_inventorie():
    """Même garantie côté modules : tout `mcp.run(transport="http"…)` de
    `klody_mcp/` doit avoir un défaut lisible par l'inventaire."""
    par_source = {d.source.rsplit(":", 1)[0] for d in inventaire(REPO)}
    serveurs = [m for m in modules_mcp(REPO) if _serveur_http(m)]
    assert len(serveurs) >= 10, "l'inventaire ne voit presque aucun serveur HTTP"
    aveugles = [
        m.relative_to(REPO).as_posix()
        for m in serveurs
        if m.relative_to(REPO).as_posix() not in par_source
    ]
    assert not aveugles, (
        "serveurs MCP HTTP dont l'inventaire ne lit aucun port par défaut :\n  "
        + "\n  ".join(aveugles)
    )


def test_la_memoire_est_lue_dans_ses_trois_sources():
    """Témoin de la lecture des trois FORMES de fichier (AST Python, lanceur
    shell, `.env.example`) sur le service de l'incident — un extracteur qui
    en raterait une rendrait la règle aveugle à ce qu'elle déclare."""
    sources = {
        d.source.rsplit(":", 1)[0]
        for d in inventaire(REPO)
        if d.cle == "MEMORY_MCP_PORT"
    }
    assert sources == {
        "klody_mcp/memory_server.py",
        "scripts/start-memory-mcp.sh",
        ".env.example",
    }


# --------------------------------------------------------------------------- #
# La règle                                                                    #
# --------------------------------------------------------------------------- #


def test_aucun_port_par_defaut_partage_entre_deux_serveurs():
    trouve = collisions(inventaire(REPO))
    assert not trouve, _rapport(
        "ports MCP par défaut revendiqués par plusieurs services "
        "(deux démons launchd sur un même bind : l'un boucle, l'autre perd "
        "son port selon l'ordre de démarrage) :",
        trouve,
    )


def test_un_service_un_seul_port_par_defaut():
    """Le module, son lanceur et `.env.example` disent le même port. Sans ça,
    un correctif posé dans le module seul laisse le lanceur — donc le démon
    launchd — sur l'ancien port, et la règle ci-dessus jugerait un port que
    personne n'écoute."""
    trouve = divergences(inventaire(REPO))
    assert not trouve, _rapport(
        "service dont le port par défaut diffère selon le fichier :", trouve
    )


# --------------------------------------------------------------------------- #
# La règle sait rougir — rejeu de l'état du 2026-09-30                        #
# --------------------------------------------------------------------------- #


def _copie_des_sources(racine: Path) -> Path:
    """Copie des seuls fichiers que lit l'inventaire."""
    cible = racine / "depot"
    (cible / "klody_mcp").mkdir(parents=True)
    (cible / "scripts").mkdir()
    for module in modules_mcp(REPO):
        shutil.copy2(module, cible / "klody_mcp" / module.name)
    for lanceur in lanceurs_mcp(REPO):
        shutil.copy2(lanceur, cible / "scripts" / lanceur.name)
    for nom in ("config.py", ".env.example"):
        shutil.copy2(REPO / nom, cible / nom)
    return cible


def _repointe(depot: Path, var: str, port: int, fichiers: list[str]) -> int:
    """Réécrit le défaut de `var` dans `fichiers` ; rend le nombre de
    réécritures. `var", "8100"`, `var:-8100}`, `var=8100` : quelques
    caractères non numériques entre le nom et le nombre."""
    motif = re.compile(rf"({re.escape(var)}[^\d\n]{{1,8}})\d+")
    total = 0
    for rel in fichiers:
        chemin = depot / rel
        texte, n = motif.subn(rf"\g<1>{port}", chemin.read_text(encoding="utf-8"))
        chemin.write_text(texte, encoding="utf-8")
        total += n
    return total


def _port_de(defauts: list[Defaut], cle: str) -> int:
    ports = {d.port for d in defauts if d.cle == cle}
    assert len(ports) == 1, f"{cle} : {ports}"
    return ports.pop()


def test_rejeu_memory_sur_le_port_de_blender_rougit(tmp_path):
    """L'incident, rejoué sur une copie : `MEMORY_MCP_PORT` ramené au port de
    Blender dans ses trois sources. La règle DOIT nommer les deux services."""
    depot = _copie_des_sources(tmp_path)
    blender = _port_de(inventaire(depot), "klody_mcp/blender_server.py")
    n = _repointe(
        depot, "MEMORY_MCP_PORT", blender,
        ["klody_mcp/memory_server.py", "scripts/start-memory-mcp.sh", ".env.example"],
    )
    assert n == 3, f"rejeu incomplet : {n} réécriture(s) au lieu de 3"

    trouve = collisions(inventaire(depot))
    assert set(trouve) == {blender}
    assert set(trouve[blender]) == {"MEMORY_MCP_PORT", "klody_mcp/blender_server.py"}
    # Et c'est bien un seul service de chaque côté, pas une divergence.
    assert divergences(inventaire(depot)) == {}


def test_rejeu_correctif_pose_dans_le_module_seul_rougit(tmp_path):
    """Le demi-correctif : le module déménage, le lanceur (donc le démon
    launchd) reste sur l'ancien port. La règle de cohérence DOIT le voir."""
    depot = _copie_des_sources(tmp_path)
    actuel = _port_de(inventaire(depot), "MEMORY_MCP_PORT")
    n = _repointe(depot, "MEMORY_MCP_PORT", actuel + 1000, ["klody_mcp/memory_server.py"])
    assert n == 1

    trouve = divergences(inventaire(depot))
    assert set(trouve) == {"MEMORY_MCP_PORT"}
    assert set(trouve["MEMORY_MCP_PORT"]) == {actuel, actuel + 1000}


def test_la_prose_ne_reserve_aucun_port(tmp_path):
    """Un commentaire ou une docstring qui cite le port d'un autre service
    n'est pas une collision — sinon le test pousserait à effacer les
    explications (« 8083 entrait en collision avec MLX_CODE_PORT »)."""
    depot = _copie_des_sources(tmp_path)
    blender = _port_de(inventaire(depot), "klody_mcp/blender_server.py")
    memoire = depot / "klody_mcp" / "memory_server.py"
    memoire.write_text(
        memoire.read_text(encoding="utf-8")
        + f"\n# PORT = {blender} était le défaut du 2026-08-16.\n"
        + f'_NOTE = "pas :{blender}, c\'est Blender (port={blender})"\n',
        encoding="utf-8",
    )
    assert collisions(inventaire(depot)) == collisions(inventaire(REPO))
