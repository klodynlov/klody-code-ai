"""
Parser d'exports JSON provenant d'autres LLMs (ChatGPT, Claude, Gemini, etc.)
Extrait les messages utilisateur pour permettre à Klody d'apprendre les pratiques.
"""

import json
import re
from pathlib import Path
_KLODY_ROOT = Path(__file__).parent.parent
IMPORTS_DIR = _KLODY_ROOT / "imports"
IMPORTS_DIR.mkdir(exist_ok=True)

# Taille max d'un extrait retourné au LLM
MAX_CHARS = 12_000


# ── Détection de format ────────────────────────────────────────────────────────

def _detect_format(data) -> str:
    """Détecte le format de l'export."""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if "mapping" in first and "title" in first:
                return "chatgpt"
            if "chat_messages" in first and "uuid" in first:
                return "claude"
            if "messages" in first:
                return "generic_list"
    if isinstance(data, dict):
        if "messages" in data:
            return "generic_dict"
    return "unknown"


# ── Parsers par format ─────────────────────────────────────────────────────────

def _parse_chatgpt(data: list) -> list[dict]:
    """
    Format ChatGPT (conversations.json) :
    [{title, mapping: {id: {message: {author: {role}, content: {parts: [str]}}}}}]
    """
    conversations = []
    for conv in data:
        title = conv.get("title", "")
        mapping = conv.get("mapping", {})
        msgs = []
        for node in mapping.values():
            m = node.get("message")
            if not m:
                continue
            role = m.get("author", {}).get("role", "")
            if role not in ("user", "assistant"):
                continue
            parts = m.get("content", {}).get("parts", [])
            text = " ".join(p for p in parts if isinstance(p, str)).strip()
            if text:
                msgs.append({"role": role, "content": text})
        if msgs:
            conversations.append({"title": title, "messages": msgs})
    return conversations


def _parse_claude(data: list) -> list[dict]:
    """
    Format Claude.ai (export JSON) :
    [{uuid, name, chat_messages: [{sender: "human"|"assistant", text: str}]}]
    """
    conversations = []
    for conv in data:
        title = conv.get("name", conv.get("uuid", ""))
        msgs = []
        for m in conv.get("chat_messages", []):
            role = "user" if m.get("sender") == "human" else "assistant"
            text = m.get("text", "").strip()
            if text:
                msgs.append({"role": role, "content": text})
        if msgs:
            conversations.append({"title": title, "messages": msgs})
    return conversations


def _parse_generic(data) -> list[dict]:
    """Tente de parser tout JSON avec une clé 'messages'."""
    if isinstance(data, dict):
        data = [data]
    conversations = []
    for item in data:
        msgs_raw = item.get("messages", [])
        msgs = []
        for m in msgs_raw:
            role = m.get("role", m.get("sender", ""))
            if role in ("human", "user"):
                role = "user"
            elif role in ("assistant", "ai", "bot"):
                role = "assistant"
            else:
                continue
            content = m.get("content", m.get("text", ""))
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            if content.strip():
                msgs.append({"role": role, "content": content.strip()})
        if msgs:
            conversations.append({"title": item.get("title", ""), "messages": msgs})
    return conversations


# ── Extraction des pratiques ───────────────────────────────────────────────────

_TECH_PATTERNS = [
    r"\b(Python|TypeScript|JavaScript|Rust|Go|Swift|Kotlin|Java|C\+\+|C#|Ruby|PHP|Scala)\b",
    r"\b(React|Vue|Angular|Next\.js|Nuxt|Svelte|FastAPI|Django|Flask|Express|NestJS)\b",
    r"\b(Tauri|Electron|SwiftUI|UIKit|Jetpack Compose|Flutter)\b",
    r"\b(PostgreSQL|MySQL|SQLite|MongoDB|Redis|Supabase|Firebase)\b",
    r"\b(Docker|Kubernetes|GitHub Actions|Terraform|AWS|GCP|Azure)\b",
    r"\b(Ollama|LangChain|LlamaIndex|MLX|PyTorch|TensorFlow|HuggingFace)\b",
    r"\b(Vite|Webpack|esbuild|Bun|pnpm|Poetry|uv)\b",
    r"\b(Tailwind|shadcn|MUI|Chakra|Radix)\b",
    r"\b(git|GitHub|GitLab|Linear|Jira|Notion)\b",
]

_CODE_BLOCK_RE = re.compile(r"```(\w*)\n?([\s\S]*?)```")


def _extract_user_messages(conversations: list[dict]) -> list[str]:
    return [
        m["content"]
        for conv in conversations
        for m in conv["messages"]
        if m["role"] == "user"
    ]


def _count_techs(messages: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    text = "\n".join(messages)
    for pat in _TECH_PATTERNS:
        for match in re.finditer(pat, text, re.IGNORECASE):
            tech = match.group(0)
            counts[tech] = counts.get(tech, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _extract_code_snippets(messages: list[str]) -> list[tuple[str, str]]:
    snippets = []
    for msg in messages:
        for m in _CODE_BLOCK_RE.finditer(msg):
            lang = m.group(1) or "text"
            code = m.group(2).strip()
            if len(code) > 30:
                snippets.append((lang, code[:300]))
    return snippets[:20]


def _build_summary(conversations: list[dict], path: Path) -> str:
    user_msgs = _extract_user_messages(conversations)
    techs = _count_techs(user_msgs)
    snippets = _extract_code_snippets(user_msgs)

    n_convs = len(conversations)
    n_msgs = len(user_msgs)

    lines = [
        f"## Import: {path.name}",
        f"- {n_convs} conversations, {n_msgs} messages utilisateur analysés",
        "",
        "### Technologies détectées (occurrences)",
    ]
    if techs:
        for tech, count in list(techs.items())[:25]:
            lines.append(f"  - {tech}: {count}×")
    else:
        lines.append("  - Aucune technologie clairement identifiée")

    lines += ["", "### Exemples de questions posées"]
    for msg in user_msgs[:8]:
        preview = msg[:120].replace("\n", " ")
        lines.append(f"  - {preview}…" if len(msg) > 120 else f"  - {msg}")

    if snippets:
        lines += ["", "### Extraits de code trouvés"]
        for lang, code in snippets[:5]:
            lines.append(f"  [{lang}] {code[:80].replace(chr(10), ' ')}…")

    lines += [
        "",
        "### Instructions pour Klody",
        "Utilise ces données pour comprendre les pratiques, langages préférés et habitudes "
        "de l'utilisateur. Tu peux appeler save_skill pour mémoriser les patterns importants.",
    ]

    return "\n".join(lines)


# ── Point d'entrée principal ───────────────────────────────────────────────────

def import_llm_export(path: str) -> str:
    """
    Lit et analyse un export JSON d'un autre LLM.
    Retourne un résumé structuré des pratiques détectées.
    """
    p = Path(path)
    if not p.is_absolute():
        p = IMPORTS_DIR / path

    if not p.exists():
        # Chercher dans imports/ si non trouvé
        candidate = IMPORTS_DIR / p.name
        if candidate.exists():
            p = candidate
        else:
            available = [f.name for f in IMPORTS_DIR.iterdir() if f.suffix == ".json"]
            hint = f"\nFichiers disponibles dans imports/ : {available}" if available else ""
            return f"ERREUR: Fichier introuvable — {path}{hint}"

    if p.suffix.lower() != ".json":
        return "ERREUR: Seuls les fichiers .json sont supportés."

    if p.stat().st_size > 100 * 1024 * 1024:
        return "ERREUR: Fichier trop volumineux (> 100 MB)."

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"ERREUR: JSON invalide — {e}"

    fmt = _detect_format(data)

    if fmt == "chatgpt":
        conversations = _parse_chatgpt(data)
    elif fmt == "claude":
        conversations = _parse_claude(data)
    elif fmt in ("generic_list", "generic_dict"):
        conversations = _parse_generic(data)
    else:
        return (
            "ERREUR: Format non reconnu. Formats supportés : ChatGPT (conversations.json), "
            "Claude (export JSON), ou tout JSON avec une clé 'messages'."
        )

    if not conversations:
        return "Aucun message trouvé dans ce fichier."

    summary = _build_summary(conversations, p)
    return summary[:MAX_CHARS]


def list_imports() -> str:
    """Liste les fichiers disponibles dans le répertoire imports/."""
    files = sorted(IMPORTS_DIR.glob("*.json"))
    if not files:
        return f"Aucun fichier JSON dans {IMPORTS_DIR}. Dépose tes exports ici."
    lines = [f"Fichiers disponibles dans {IMPORTS_DIR} :"]
    for f in files:
        size_kb = f.stat().st_size // 1024
        lines.append(f"  - {f.name}  ({size_kb} KB)")
    return "\n".join(lines)
