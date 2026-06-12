#!/usr/bin/env python
"""Backfill de la mémoire sémantique (agent/semantic_memory) depuis l'existant.

Deux gisements :
  1. logs/long_term.json — les faits de LongTermMemory, Y COMPRIS ceux au-delà du
     cap d'injection du prompt (seuls les nouveaux écrits passent par le miroir ;
     le stock historique doit être indexé une fois ici).
  2. logs/memory_*.json — les sessions passées : titre + messages utilisateur
     condensés, kind="session". Rend les sessions RETROUVABLES sémantiquement
     (« sur quoi avait-on travaillé pour X ? »), ce que les JSON plats ne
     permettent pas.

Idempotent : remember(replace=True) déduplique par (title, kind) → relancer le
script ré-indexe les nouvelles sessions sans dupliquer les anciennes.

Usage :
    .venv/bin/python scripts/backfill_semantic_memory.py [--no-facts] [--no-sessions]
                                                         [--limit N] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agent import semantic_memory

# Bornes de condensation d'une session : assez pour la retrouver, pas un dump.
_SESSION_TEXT_CAP = 1500
_SESSION_TITLE_CAP = 120


def backfill_facts(db: Path | None) -> tuple[int, int]:
    """Indexe logs/long_term.json. Retourne (indexés, échecs)."""
    storage = config.MEMORY_DIR / "long_term.json"
    if not storage.exists():
        print(f"  (pas de {storage.name} — rien à faire)")
        return 0, 0
    entries = json.loads(storage.read_text(encoding="utf-8"))
    done = failed = 0
    for e in entries:
        key, content = e.get("key", ""), e.get("content", "")
        if not key or not content:
            continue
        try:
            semantic_memory.remember(
                content, title=key, kind=e.get("category", "context"),
                replace=True, db_path=db,
            )
            done += 1
        except Exception as exc:
            failed += 1
            print(f"  ÉCHEC fait '{key}': {exc}")
    return done, failed


def _condense_session(data: dict) -> str | None:
    """Titre + date + messages utilisateur, capé. None si rien d'indexable."""
    user_texts = [
        (m.get("content") or "").strip()
        for m in data.get("messages", [])
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    if not user_texts:
        return None
    created = (data.get("created_at") or "")[:10]
    title = data.get("title") or "(sans titre)"
    text = f"Session du {created} — {title}\n" + "\n".join(user_texts)
    return text[:_SESSION_TEXT_CAP]


def backfill_sessions(db: Path | None, limit: int | None) -> tuple[int, int, int]:
    """Indexe logs/memory_*.json. Retourne (indexées, sautées, échecs)."""
    files = sorted(config.MEMORY_DIR.glob("memory_*.json"))
    if limit:
        files = files[-limit:]
    done = skipped = failed = 0
    for i, path in enumerate(files, 1):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = data.get("session_id") or path.stem.removeprefix("memory_")
            text = _condense_session(data)
            if text is None:
                skipped += 1
                continue
            semantic_memory.remember(
                text,
                title=f"session:{sid}",
                author=(data.get("title") or "")[:_SESSION_TITLE_CAP] or None,
                kind="session",
                replace=True,
                db_path=db,
            )
            done += 1
        except Exception as exc:
            failed += 1
            print(f"  ÉCHEC session {path.name}: {exc}")
        if i % 100 == 0:
            print(f"  … {i}/{len(files)} sessions traitées")
    return done, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-facts", action="store_true", help="sauter long_term.json")
    parser.add_argument("--no-sessions", action="store_true", help="sauter les sessions")
    parser.add_argument("--limit", type=int, default=0,
                        help="ne traiter que les N sessions les plus récentes")
    parser.add_argument("--db", type=Path, default=None,
                        help=f"base cible (défaut {config.SEMANTIC_MEMORY_DB})")
    args = parser.parse_args()

    if not semantic_memory.MEMORY_AVAILABLE:
        print("klody-memory non installé — abandon.")
        return 1

    db = args.db
    print(f"Base : {db or config.SEMANTIC_MEMORY_DB}")
    print(f"Provider embeddings : {config.SEMANTIC_MEMORY_PROVIDER}")

    if not args.no_facts:
        print("— Faits long-terme (long_term.json) :")
        done, failed = backfill_facts(db)
        print(f"  {done} indexés, {failed} échecs")

    if not args.no_sessions:
        print("— Sessions (memory_*.json) :")
        done, skipped, failed = backfill_sessions(db, args.limit or None)
        print(f"  {done} indexées, {skipped} sautées (vides), {failed} échecs")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
