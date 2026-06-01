"""
Mémoire longue terme de Klody.

Persiste des faits entre les sessions dans MEMORY_DIR/long_term.json.
Chaque entrée : {key, content, category, updated_at}

Catégories :
  user        — qui est l'utilisateur, son niveau, son workflow
  project     — projets en cours, leur état, leurs contraintes
  preference  — préférences de style, outils favoris, habitudes
  context     — tout autre fait utile à long terme
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from config import MEMORY_DIR

logger = logging.getLogger(__name__)

Category = Literal["user", "project", "preference", "context"]

_STORAGE = MEMORY_DIR / "long_term.json"

_CATEGORY_LABELS = {
    "user":       "Utilisateur",
    "project":    "Projets en cours",
    "preference": "Préférences",
    "context":    "Contexte général",
}


class LongTermMemory:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    # API publique                                                         #
    # ------------------------------------------------------------------ #

    def remember(self, key: str, content: str, category: Category = "context") -> str:
        """Mémorise ou met à jour un fait. Retourne un message de confirmation."""
        key = key.strip().lower().replace(" ", "_")
        if not key or not content.strip():
            return "ERREUR: key et content sont requis."

        for entry in self.entries:
            if entry["key"] == key:
                entry["content"] = content.strip()
                entry["category"] = category
                entry["updated_at"] = datetime.now().isoformat()
                self._save()
                return f"Mémoire mise à jour : [{category}] {key}"

        self.entries.append({
            "key": key,
            "content": content.strip(),
            "category": category,
            "updated_at": datetime.now().isoformat(),
        })
        self._save()
        return f"Mémorisé : [{category}] {key}"

    def forget(self, key: str) -> str:
        """Supprime un fait. Retourne un message de confirmation."""
        key = key.strip().lower().replace(" ", "_")
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["key"] != key]
        if len(self.entries) == before:
            return f"Clé introuvable : '{key}'"
        self._save()
        return f"Oublié : {key}"

    def list_all(self) -> list[dict]:
        """Retourne toutes les entrées triées par catégorie."""
        return sorted(self.entries, key=lambda e: (e["category"], e["key"]))

    def format_for_prompt(self) -> str:
        """Formate la mémoire pour injection dans le system prompt."""
        if not self.entries:
            return ""

        by_cat: dict[str, list[dict]] = {}
        for entry in self.entries:
            cat = entry.get("category", "context")
            by_cat.setdefault(cat, []).append(entry)

        lines = ["\n\n## Mémoire longue terme (entre les sessions)\n"]
        for cat in ("user", "project", "preference", "context"):
            items = by_cat.get(cat, [])
            if not items:
                continue
            lines.append(f"**{_CATEGORY_LABELS[cat]}** :")
            for item in sorted(items, key=lambda e: e["key"]):
                lines.append(f"- {item['key']} : {item['content']}")
            lines.append("")

        lines.append(
            "_Utilise ces informations pour personnaliser tes réponses. "
            "Tu peux mettre à jour la mémoire avec remember_fact / forget_fact._"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Persistance                                                          #
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        try:
            _STORAGE.write_text(
                json.dumps(self.entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("[LongTermMemory] Erreur sauvegarde : %s", e)

    def _load(self) -> None:
        if not _STORAGE.exists():
            return
        try:
            self.entries = json.loads(_STORAGE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("[LongTermMemory] Erreur chargement : %s", e)
            self.entries = []


# Singleton partagé
_instance: LongTermMemory | None = None


def get_long_term_memory() -> LongTermMemory:
    global _instance
    if _instance is None:
        _instance = LongTermMemory()
    return _instance
