#!/usr/bin/env python
"""Banc de la gate MISSION-D 6.2 — « 3 kicks qui collent » sur bench/corpus.

Pour chaque morceau du corpus (10 : 5 carib + 5 varied) :
  1. `atelier.analyser_morceau` (cache hub-v2 : séparation + O&F + kicks/) ;
  2. `samplebrain.chercher_kicks_pour_morceau(job_id, k=3)` ;
  3. vérifie : 3 kicks rendus, chacun classe `kick` ET durée < 1 s (one-shot).

Gate : ≥ 9/10 morceaux avec 3/3 kicks conformes. Écrit `bench_kicks_62.json`
+ `bench_kicks_62.md` dans `--out`. Les bibliothèques SampleBrain viennent de
`SAMPLEBRAIN_URLS` (poser des serveurs de test pour ne pas toucher la prod).

    SAMPLEBRAIN_URLS=principale=http://127.0.0.1:8801,externe=http://127.0.0.1:8802 \\
      .venv/bin/python scripts/bench_kicks_62.py --out ~/Archives/spikes/kicks-62-20260916
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Le corpus vit hors des racines audio de prod : on les élargit POUR CE BANC
# seulement (la garde se lit à l'import de klody_mcp).
CORPUS = Path("~/suite-musicale/bench/corpus").expanduser()
_H = Path.home()
os.environ.setdefault("KLODY_MCP_AUDIO_ROOTS", os.pathsep.join(str(p) for p in (
    _H / "Music", _H / "Documents", _H / "Projets", _H / ".klody", CORPUS)))

from klody_mcp import (  # noqa: E402 — après l'élargissement des racines
    atelier_server as at,
    samplebrain_server as sb,
)


def _attendre(job_id: str, timeout: float = 600.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = at.statut_analyse(job_id)
        if st.get("status") in ("done", "failed"):
            return st
        time.sleep(3)
    return {"status": "timeout"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--corpus", default=str(CORPUS))
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    wavs = sorted(Path(args.corpus).glob("*.wav"))
    if not wavs:
        print(f"corpus vide : {args.corpus}", file=sys.stderr)
        return 2

    lignes = []
    for wav in wavs:
        t0 = time.time()
        lance = at.analyser_morceau(str(wav))
        job_id = lance.get("job_id")
        st = _attendre(job_id) if job_id else {"status": "failed", "detail": lance}
        t_analyse = round(time.time() - t0, 1)
        t1 = time.time()
        rep = sb.chercher_kicks_pour_morceau(job_id, k=args.k) if job_id else {"erreur": str(lance)}
        t_kicks = round(time.time() - t1, 2)
        kicks = rep.get("kicks") or []
        conformes = [k for k in kicks
                     if k.get("classe") == "kick" and k.get("secondes") is not None
                     and float(k["secondes"]) < 1.0]
        ok = len(kicks) == args.k and len(conformes) == args.k
        lignes.append({
            "morceau": wav.name, "job_id": job_id, "statut_analyse": st.get("status"),
            "cache": lance.get("status") == "done", "t_analyse_s": t_analyse, "t_kicks_s": t_kicks,
            "n_kicks_morceau": rep.get("n_kicks_dans_le_morceau"),
            "kicks": [{"fichier": k.get("fichier"), "chemin": k.get("chemin"),
                       "secondes": k.get("secondes"), "classe": k.get("classe"),
                       "score_rrf": k.get("score_rrf"), "distance_min": k.get("distance_min"),
                       "votes": k.get("tranches_votantes"), "bibliotheque": k.get("bibliotheque")}
                      for k in kicks],
            "tranches": rep.get("tranches"),
            "n_conformes": len(conformes), "ok": ok,
            "erreur": rep.get("erreur"), "muettes": rep.get("bibliotheques_muettes"),
        })
        print(f"{wav.name:40s} analyse={st.get('status'):7s} {t_analyse:6.1f}s  "
              f"kicks={len(kicks)} conformes={len(conformes)} {'✅' if ok else '❌'} "
              f"{rep.get('erreur') or ''}", flush=True)

    n_ok = sum(1 for ln in lignes if ln["ok"])
    verdict = {"n_morceaux": len(lignes), "n_ok": n_ok, "gate_9_sur_10": n_ok >= 9,
               "k": args.k, "date": time.strftime("%Y-%m-%d %H:%M"),
               "bibliotheques": sb.BIBLIOTHEQUES, "lignes": lignes}
    (out / "bench_kicks_62.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
    md = [f"# Gate 6.2 — kicks one-shots par audio · {verdict['date']}", "",
          f"**{n_ok}/{len(lignes)} morceaux** avec {args.k}/{args.k} kicks classe `kick` et < 1 s "
          f"→ gate ≥ 9/10 : {'✅' if verdict['gate_9_sur_10'] else '❌'}", "",
          "| morceau | kicks du morceau | t analyse | t kicks | hits (fichier · s · votes) | ok |",
          "|---|---|---|---|---|---|"]
    for ln in lignes:
        hits = "<br>".join(f"{k['fichier']} · {k['secondes']} s · {k['votes']}" for k in ln["kicks"]) or (ln["erreur"] or "—")
        md.append(f"| {ln['morceau']} | {ln['n_kicks_morceau']} | {ln['t_analyse_s']} s | {ln['t_kicks_s']} s | {hits} | "
                  f"{'✅' if ln['ok'] else '❌'} |")
    (out / "bench_kicks_62.md").write_text("\n".join(md) + "\n")
    print(f"\n{n_ok}/{len(lignes)} → gate {'OK' if verdict['gate_9_sur_10'] else 'KO'} ; {out}")
    return 0 if verdict["gate_9_sur_10"] else 1


if __name__ == "__main__":
    sys.exit(main())
