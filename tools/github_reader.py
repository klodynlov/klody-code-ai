"""Lecture de dépôts GitHub : arbre, fichiers source, dépôts indexés, bonnes pratiques."""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

import httpx
from config import GITHUB_TOKEN, LIBRARYBRAIN_URL, librarybrain_headers

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_LB_BASE = LIBRARYBRAIN_URL.rsplit("/api/", 1)[0] if "/api/" in LIBRARYBRAIN_URL else ""


def _gh_get(url: str, token: str = "", timeout: int = 15) -> dict | list | None:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    tok = token or GITHUB_TOKEN
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        # URL construite en interne contre api.github.com, pas d'input utilisateur libre
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.debug("[github_reader] HTTP %s pour %s", exc.code, url)
        return None
    except Exception as exc:
        logger.debug("[github_reader] Erreur réseau : %s", exc)
        return None


def _parse_owner_repo(repo_ref: str) -> tuple[str, str]:
    """Accepte 'owner/repo' ou 'https://github.com/owner/repo'."""
    ref = repo_ref.strip().rstrip("/")
    if ref.startswith("https://github.com/"):
        ref = ref.replace("https://github.com/", "")
    parts = ref.split("/")
    if len(parts) < 2:
        raise ValueError(f"Format attendu : owner/repo — reçu : '{repo_ref}'")
    return parts[0], parts[1]


def browse_repo(repo_ref: str, path: str = "", recursive: bool = False) -> str:
    """Parcourt l'arbre d'un dépôt GitHub. Retourne la structure fichiers/dossiers."""
    owner, repo = _parse_owner_repo(repo_ref)

    if recursive:
        url = f"{_API}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        data = _gh_get(url)
        if not data or "tree" not in data:
            return f"Impossible de lire l'arbre de {owner}/{repo}."
        entries = data["tree"]
        if path:
            prefix = path.rstrip("/") + "/"
            entries = [e for e in entries if e["path"].startswith(prefix)]
        lines = []
        for e in sorted(entries, key=lambda x: x["path"]):
            icon = "📁" if e["type"] == "tree" else "📄"
            size = f"  ({e['size']} B)" if e.get("size") else ""
            lines.append(f"{icon} {e['path']}{size}")
        if not lines:
            return f"Aucun fichier trouvé dans {owner}/{repo}/{path}."
        return f"📦 {owner}/{repo}\n" + "\n".join(lines)

    url = f"{_API}/repos/{owner}/{repo}/contents/{path}"
    items = _gh_get(url)
    if not isinstance(items, list):
        return f"Impossible de lister {owner}/{repo}/{path}."
    lines = []
    dirs = sorted([i for i in items if i["type"] == "dir"], key=lambda x: x["name"])
    files = sorted([i for i in items if i["type"] != "dir"], key=lambda x: x["name"])
    for d in dirs:
        lines.append(f"📁 {d['name']}/")
    for f in files:
        size = f"  ({f.get('size', '?')} B)" if f.get("size") else ""
        lines.append(f"📄 {f['name']}{size}")
    return f"📦 {owner}/{repo}/{path}\n" + "\n".join(lines)


def read_github_file(repo_ref: str, path: str) -> str:
    """Lit le contenu d'un fichier depuis un dépôt GitHub."""
    owner, repo = _parse_owner_repo(repo_ref)
    url = f"{_API}/repos/{owner}/{repo}/contents/{path}"
    data = _gh_get(url)
    if not data or not isinstance(data, dict):
        return f"Fichier introuvable : {owner}/{repo}/{path}"
    if data.get("type") != "file":
        return f"{path} n'est pas un fichier (type: {data.get('type')})."
    content_b64 = data.get("content", "")
    encoding = data.get("encoding", "")
    if encoding == "base64" and content_b64:
        try:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception as exc:
            return f"Erreur décodage : {exc}"
    download_url = data.get("download_url", "")
    if download_url:
        req = urllib.request.Request(download_url)
        tok = GITHUB_TOKEN
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            # download_url renvoyé par l'API GitHub authentifiée
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return f"Erreur téléchargement : {exc}"
    return "Contenu non disponible."


def list_indexed_repos() -> str:
    """Liste les dépôts GitHub déjà indexés dans LibraryBrain."""
    if not _LB_BASE:
        return "LibraryBrain non configuré."
    try:
        with httpx.Client(timeout=10.0, headers=librarybrain_headers()) as client:
            resp = client.get(f"{_LB_BASE}/api/books", params={"category": "GitHub", "limit": 100})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("[github_reader] Erreur LibraryBrain : %s", exc)
        return f"Impossible de contacter LibraryBrain : {exc}"

    books = data if isinstance(data, list) else data.get("books", [])
    if not books:
        return "Aucun dépôt GitHub indexé dans LibraryBrain."

    repos: dict[str, list[str]] = {}
    for b in books:
        fp = b.get("file_path", "")
        if fp.startswith("github://"):
            parts = fp.replace("github://", "").split("/", 2)
            if len(parts) >= 2:
                key = f"{parts[0]}/{parts[1]}"
                file = parts[2] if len(parts) > 2 else ""
                repos.setdefault(key, []).append(file)

    if not repos:
        return "Aucun dépôt GitHub identifié dans les livres indexés."

    lines = [f"📚 {len(repos)} dépôt(s) GitHub indexé(s) dans LibraryBrain :\n"]
    for repo_name, files in sorted(repos.items()):
        lines.append(f"  📦 {repo_name} — {len(files)} fichier(s) indexé(s)")
        for f in sorted(files)[:5]:
            if f:
                lines.append(f"     📄 {f}")
        if len(files) > 5:
            lines.append(f"     … et {len(files) - 5} autre(s)")
    return "\n".join(lines)


def index_github_repo(repo_ref: str) -> str:
    """Indexe un dépôt GitHub dans LibraryBrain (README + docs)."""
    owner, repo = _parse_owner_repo(repo_ref)
    if not _LB_BASE:
        return "LibraryBrain non configuré."
    try:
        # Écriture dans LibraryBrain : sous api_token, c'est justement le genre
        # d'appel que l'auth est censée protéger — il doit porter le token.
        with httpx.Client(timeout=60.0, headers=librarybrain_headers()) as client:
            resp = client.post(
                f"{_LB_BASE}/api/github/index",
                json={"repo": f"{owner}/{repo}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        return "LibraryBrain inaccessible — serveur non démarré."
    except Exception as exc:
        return f"Erreur indexation GitHub : {exc}"

    added = data.get("added", 0)
    updated = data.get("updated", 0)
    files = data.get("files", [])
    return (
        f"✅ {owner}/{repo} indexé dans LibraryBrain : "
        f"{added} ajouté(s), {updated} mis à jour.\n"
        f"Fichiers : {', '.join(files) if files else 'aucun'}"
    )


def extract_best_practices(repo_ref: str) -> str:
    """Analyse la structure d'un dépôt et identifie les bonnes pratiques.

    Lit les fichiers clés (README, setup.cfg/pyproject.toml, Makefile, CI, etc.)
    et retourne un résumé structuré que le LLM utilisera pour save_skill.
    """
    owner, repo = _parse_owner_repo(repo_ref)

    key_files = [
        "README.md", "readme.md",
        "pyproject.toml", "setup.cfg", "setup.py", "package.json", "Cargo.toml",
        "Makefile", "Taskfile.yml",
        ".github/workflows/ci.yml", ".github/workflows/ci.yaml",
        ".github/workflows/test.yml", ".github/workflows/tests.yml",
        ".pre-commit-config.yaml",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        ".flake8", ".pylintrc", "ruff.toml", ".eslintrc.json", ".eslintrc.js",
        "tsconfig.json", "tox.ini", "noxfile.py",
        "CONTRIBUTING.md", "ARCHITECTURE.md",
    ]

    found: dict[str, str] = {}
    for f in key_files:
        content = read_github_file(f"{owner}/{repo}", f)
        if not content.startswith(("Fichier introuvable", "Erreur", "Contenu non")):
            found[f] = content[:8000]

    if not found:
        return f"Aucun fichier de configuration trouvé dans {owner}/{repo}."

    url = f"{_API}/repos/{owner}/{repo}"
    meta = _gh_get(url)

    url_tree = f"{_API}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    tree_data = _gh_get(url_tree)
    tree_paths = []
    if tree_data and "tree" in tree_data:
        tree_paths = [e["path"] for e in tree_data["tree"]]

    sections = [f"# Analyse des bonnes pratiques : {owner}/{repo}\n"]

    if meta and isinstance(meta, dict):
        lang = meta.get("language", "inconnu")
        desc = meta.get("description", "")
        stars = meta.get("stargazers_count", 0)
        sections.append(f"**Langage principal** : {lang}")
        if desc:
            sections.append(f"**Description** : {desc}")
        sections.append(f"**Stars** : {stars}")
        sections.append("")

    sections.append("## Structure du projet")
    top_level = sorted(set(p.split("/")[0] for p in tree_paths))[:30]
    sections.append("```")
    sections.append("\n".join(top_level))
    sections.append("```\n")

    for filename, content in found.items():
        sections.append(f"## {filename}")
        sections.append(f"```\n{content[:4000]}\n```\n")

    return "\n".join(sections)
