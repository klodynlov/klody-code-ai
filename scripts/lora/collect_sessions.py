"""Collecte des sessions Klody pour fine-tuning LoRA (Roadmap v2 #9).

Lit tous les fichiers `logs/memory_*.json` et extrait les paires
(prompt utilisateur → réponse assistant + tool calls) au format JSONL
compatible avec `mlx_lm.lora`.

Filtres :
- Sessions sans message utilisateur → skip
- Réponses vides → skip
- Optionnel : ne garder que les sessions où la réponse a généré ≥ 1 tool call
  réussi (proxy de qualité)

Format de sortie (chat) :
    {"messages": [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]}

Usage :
    python -m scripts.lora.collect_sessions               # tout
    python -m scripts.lora.collect_sessions --min-tools 1 # qualité-filtré
    python -m scripts.lora.collect_sessions --out custom.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_LOGS = REPO_ROOT / "logs"
DEFAULT_OUT = REPO_ROOT / "lora" / "train.jsonl"


def _extract_pairs(session: list[dict], min_tools: int = 0) -> list[dict]:
    """Extrait les paires user → (assistant + tool calls éventuels)."""
    pairs: list[dict] = []
    i = 0
    while i < len(session):
        msg = session[i]
        if msg.get("role") != "user":
            i += 1
            continue
        user_content = (msg.get("content") or "").strip()
        if not user_content:
            i += 1
            continue

        # Collecter les messages assistant qui suivent (peut y en avoir plusieurs
        # avec tool calls + tool results entre eux)
        assistant_parts: list[str] = []
        tool_call_count = 0
        j = i + 1
        while j < len(session) and session[j].get("role") != "user":
            m = session[j]
            role = m.get("role")
            if role == "assistant":
                c = (m.get("content") or "").strip()
                if c:
                    assistant_parts.append(c)
                if m.get("tool_calls"):
                    tool_call_count += len(m["tool_calls"])
            j += 1

        if assistant_parts and tool_call_count >= min_tools:
            pairs.append({
                "messages": [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": "\n\n".join(assistant_parts)},
                ],
                "_meta": {"tool_calls": tool_call_count},
            })
        i = j
    return pairs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Collecte sessions Klody → JSONL pour LoRA")
    p.add_argument("--logs-dir", type=Path, default=DEFAULT_LOGS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-tools", type=int, default=0,
                   help="Ne garder que les paires avec ≥ N tool calls (proxy qualité)")
    p.add_argument("--strip-meta", action="store_true",
                   help="Retirer les champs _meta avant écriture (format pur mlx_lm.lora)")
    args = p.parse_args(argv)

    if not args.logs_dir.is_dir():
        print(f"❌ Logs dir introuvable : {args.logs_dir}", file=sys.stderr)
        return 1

    files = sorted(args.logs_dir.glob("memory_*.json"))
    print(f"→ {len(files)} fichiers de sessions trouvés")

    all_pairs: list[dict] = []
    for f in files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  ⚠ skip {f.name}: {exc}")
            continue
        # Format Klody : {"session_id":..., "messages": [...]}
        # ou directement une liste de messages (ancien format).
        if isinstance(raw, dict):
            messages = raw.get("messages", [])
        elif isinstance(raw, list):
            messages = raw
        else:
            continue
        pairs = _extract_pairs(messages, min_tools=args.min_tools)
        all_pairs.extend(pairs)

    print(f"→ {len(all_pairs)} paires extraites (min_tools={args.min_tools})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for p_ in all_pairs:
            if args.strip_meta:
                p_ = {k: v for k, v in p_.items() if not k.startswith("_")}
            f.write(json.dumps(p_, ensure_ascii=False) + "\n")
    print(f"📊 Écrit : {args.out}  ({args.out.stat().st_size // 1024} Ko)")

    if len(all_pairs) < 50:
        print()
        print("⚠ Moins de 50 paires — pour un LoRA utile, vise ≥ 200-500 paires.")
        print("  Utilise Klody quotidiennement et relance ce script dans quelques semaines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
