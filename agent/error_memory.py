"""Mémoire des erreurs récurrentes (Roadmap v2 #8).

Hook les échecs du sandbox auto-check : agrège les erreurs par "signature"
(type d'exception + module + message clé). Au-delà d'un seuil, suggère
proactivement la cause récurrente au modèle.

Stockage : <project>/.klody/errors.json — append-only avec rotation à 100 entrées.

Signatures détectées (Python) :
- "ModuleNotFoundError: pandas"     → "module manquant"
- "AttributeError: NoneType.x"      → "valeur None inattendue"
- "AssertionError ... test_foo"     → "test foo échoue"
- "SyntaxError ... file.py:10"      → "syntax cassée"
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


_MAX_ENTRIES = 100
_KLODY_DIR = ".klody"
_ERRORS_FILE = "errors.json"


@dataclass
class ErrorEntry:
    timestamp: float
    signature: str    # ex: "ModuleNotFoundError: pandas"
    raw_excerpt: str  # 200 chars de stderr
    command: str = ""


def _signature(stderr: str) -> str | None:
    """Extrait une signature courte d'un stderr Python."""
    if not stderr:
        return None

    # ModuleNotFoundError / ImportError
    m = re.search(r"(ModuleNotFoundError|ImportError):\s+No module named '?([\w\.]+)'?", stderr)
    if m:
        return f"{m.group(1)}: {m.group(2)}"

    # AttributeError sur NoneType
    m = re.search(r"AttributeError:\s+'NoneType'\s+object has no attribute\s+'?(\w+)'?", stderr)
    if m:
        return f"AttributeError NoneType.{m.group(1)}"

    # AttributeError générique
    m = re.search(r"AttributeError:\s+(['\"\w\s]+)", stderr)
    if m:
        return f"AttributeError: {m.group(1).strip()[:60]}"

    # SyntaxError + fichier (peut avoir plusieurs lignes entre File et SyntaxError)
    m = re.search(r'File "([^"]+)"[\s\S]*?SyntaxError:', stderr)
    if m:
        return f"SyntaxError: {m.group(1).rsplit('/', 1)[-1]}"

    # AssertionError (pytest)
    m = re.search(r"FAILED\s+([\w/.:_-]+)::(\w+)", stderr)
    if m:
        return f"AssertionError: {m.group(1)}::{m.group(2)}"

    # Fallback : prendre le dernier type d'exception
    m = re.search(r"(\w+(?:Error|Exception)):\s*(.+)", stderr.splitlines()[-1] if stderr.strip() else "")
    if m:
        return f"{m.group(1)}: {m.group(2).strip()[:60]}"

    return None


@dataclass
class ErrorMemory:
    """Append-only log des erreurs sandbox avec agrégation."""
    workdir: Path
    entries: list[ErrorEntry] = field(default_factory=list)

    def __post_init__(self):
        self.workdir = Path(self.workdir).resolve()
        self._path: Path = self.workdir / _KLODY_DIR / _ERRORS_FILE
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.entries = [ErrorEntry(**d) for d in data][-_MAX_ENTRIES:]
        except (OSError, ValueError, TypeError):
            self.entries = []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([asdict(e) for e in self.entries[-_MAX_ENTRIES:]],
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def record(self, stderr: str, command: str = "") -> str | None:
        """Enregistre une erreur. Retourne la signature si extraite, sinon None."""
        sig = _signature(stderr)
        if not sig:
            return None
        self.entries.append(ErrorEntry(
            timestamp=time.time(),
            signature=sig,
            raw_excerpt=stderr.strip()[-200:],
            command=command[:100],
        ))
        self.entries = self.entries[-_MAX_ENTRIES:]
        self._save()
        return sig

    def recurrent(self, min_count: int = 3, window_s: float = 86400) -> list[tuple[str, int]]:
        """Retourne les signatures vues ≥ min_count fois dans la fenêtre."""
        cutoff = time.time() - window_s
        recent = [e.signature for e in self.entries if e.timestamp >= cutoff]
        counter = Counter(recent)
        return [(sig, n) for sig, n in counter.most_common() if n >= min_count]

    def format_for_prompt(self, min_count: int = 3) -> str:
        """Section concise sur les erreurs récurrentes à injecter dans le system prompt."""
        recurrent = self.recurrent(min_count=min_count)
        if not recurrent:
            return ""
        lines = ["\n## Erreurs récurrentes (à éviter)"]
        for sig, n in recurrent[:5]:
            lines.append(f"- **{sig}** ({n}× vus récemment)")
        return "\n".join(lines)
