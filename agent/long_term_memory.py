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

# Cap d'injection dans le system prompt, PAR catégorie. L'extracteur auto empile
# surtout en "context" (des centaines d'entrées) : tout injecter coûtait ~7 000
# tokens À CHAQUE TOUR (la moitié du budget de contexte). On ne garde que les N
# plus RÉCENTES par catégorie ; rien n'est supprimé du disque, juste non injecté.
# Le profil curé (skills `utilisateur_*`) reste, lui, la source de référence.
_MAX_ENTRIES_PER_CATEGORY = 15

# Plafond DISQUE des entrées "context" (≫ le cap d'injection). Le cap ci-dessus
# borne ce qu'on INJECTE ; sans borne sur le DISQUE, l'extracteur auto fait
# enfler long_term.json sans fin (chargement/sauvegarde plus lents, doublons,
# pertinence diluée). prune() ne touche QUE "context" (volatil, auto-généré) ;
# user/project/preference sont curés, durables, jamais purgés.
_MAX_CONTEXT_ON_DISK = 60


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
        # GC live : si "context" dépasse le plafond disque, on purge (save inclus).
        ctx_count = sum(1 for e in self.entries if e.get("category") == "context")
        if category == "context" and ctx_count > _MAX_CONTEXT_ON_DISK and self.prune():
            return f"Mémorisé : [{category}] {key}"
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

    def prune(self) -> int:
        """GC de la mémoire : borne le flot de l'extracteur auto.

        Agit UNIQUEMENT sur la catégorie "context" (volatile, auto-générée) ;
        user/project/preference sont curés et durables, jamais purgés.

        1. Dé-duplication : deux entrées "context" au contenu normalisé identique
           → on garde la plus récente (l'extracteur ré-extrait souvent le même
           fait sous des clés différentes).
        2. Plafond disque : au-delà de `_MAX_CONTEXT_ON_DISK`, on ne garde que les
           plus récentes (updated_at desc).

        Retourne le nombre d'entrées supprimées (0 = aucun changement, pas de save).
        """
        before = len(self.entries)
        context = [e for e in self.entries if e.get("category") == "context"]
        others = [e for e in self.entries if e.get("category") != "context"]

        # 1. dédup par contenu normalisé — la plus récente gagne
        seen: dict[str, dict] = {}
        for e in sorted(context, key=lambda e: e.get("updated_at", ""), reverse=True):
            norm = " ".join((e.get("content") or "").lower().split())
            if norm and norm not in seen:
                seen[norm] = e
        deduped = sorted(
            seen.values(), key=lambda e: e.get("updated_at", ""), reverse=True
        )

        # 2. plafond disque (plus récentes d'abord)
        kept_context = deduped[:_MAX_CONTEXT_ON_DISK]

        removed = before - (len(others) + len(kept_context))
        if removed:
            self.entries = others + kept_context
            self._save()
            logger.info("[LongTermMemory] GC : %d entrée(s) 'context' purgée(s)", removed)
        return removed

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
            # Cap par catégorie : on garde les plus RÉCENTES (updated_at desc).
            # Le reste demeure sur disque, juste pas injecté dans le prompt.
            hidden = 0
            if len(items) > _MAX_ENTRIES_PER_CATEGORY:
                hidden = len(items) - _MAX_ENTRIES_PER_CATEGORY
                items = sorted(
                    items, key=lambda e: e.get("updated_at", ""), reverse=True
                )[:_MAX_ENTRIES_PER_CATEGORY]
            lines.append(f"**{_CATEGORY_LABELS[cat]}** :")
            for item in sorted(items, key=lambda e: e["key"]):
                lines.append(f"- {item['key']} : {item['content']}")
            if hidden:
                lines.append(f"_(+{hidden} faits plus anciens, non affichés)_")
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
        # GC opportuniste au démarrage : résorbe le surplus 'context' hérité.
        if self.entries:
            self.prune()


# Singleton partagé
_instance: LongTermMemory | None = None


def get_long_term_memory() -> LongTermMemory:
    global _instance
    if _instance is None:
        _instance = LongTermMemory()
    return _instance
