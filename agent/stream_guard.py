"""Garde DURE anti-répétition dégénérée pour le streaming LLM.

Les modèles locaux (MLX/Ollama), surtout à basse température et sans
`repetition_penalty`, partent parfois en boucle : ils répètent la même phrase ou
le même bloc jusqu'à épuiser tout le budget de tokens sans jamais conclure
(symptôme « il génère une boucle » ; vécu : « molécule de THC en 3D »).

`config.LLM_REPETITION_PENALTY` est un filet SOUPLE (sampling) qui ne casse pas
toujours le cycle. Ce module est le filet DUR : il détecte qu'une queue de texte
est un motif répété et signale OÙ couper le stream pour ne garder qu'une copie.

Pur (hors throttle de `LoopGuard`) → testable sans LLM.
"""
from __future__ import annotations


def degenerate_cut(
    text: str,
    *,
    reps: int = 4,
    min_unit: int = 16,
    window: int = 2000,
) -> int | None:
    """Index où tronquer `text` si sa FIN est un motif répété, sinon ``None``.

    Tronquer à l'index renvoyé (``text[:cut]``) garde le contenu légitime + UNE
    occurrence du motif et jette les répétitions suivantes.

    Deux détecteurs complémentaires :
      1. **Lignes** : les ``reps`` dernières lignes non vides sont identiques
         (capte les refrains, y compris séparés par des lignes blanches).
      2. **Sous-chaîne** : la queue vaut exactement ``motif × reps`` (capte les
         boucles sans retour à la ligne — phrases, fragments de code).

    ``min_unit`` (longueur mini du motif) écarte les répétitions LÉGITIMES
    courtes : bordure Markdown ``----``, pile d'accolades ``}``, ``===``.
    ``reps`` exige une boucle franche (défaut 4).
    """
    if not text or reps < 2:
        return None

    # ── Détecteur 1 : lignes finales identiques ──────────────────────────
    lines = text.splitlines(keepends=True)
    nonblank = [(i, ln) for i, ln in enumerate(lines) if ln.strip()]
    if len(nonblank) >= reps:
        tail = nonblank[-reps:]
        unit = tail[-1][1].strip()
        if len(unit) >= min_unit and all(ln.strip() == unit for _, ln in tail):
            first_idx = tail[0][0]  # 1re ligne répétée : on la garde, on coupe après
            return sum(len(l) for l in lines[: first_idx + 1])

    # ── Détecteur 2 : sous-chaîne répétée en queue ───────────────────────
    s = text[-window:]
    n = len(s)
    total = len(text)
    max_p = n // reps
    for p in range(min_unit, max_p + 1):
        unit = s[n - p:]
        if not unit.strip():            # queue purement blanche → pas un « texte » répété
            continue
        if s[n - reps * p:] == unit * reps:
            # Compte TOUTES les copies consécutives en queue (pas seulement `reps`)
            # pour tout effondrer sur une seule, même si le texte entier boucle.
            k = reps
            while (k + 1) * p <= total and text[total - (k + 1) * p: total - k * p] == unit:
                k += 1
            return total - (k - 1) * p  # garde 1 copie, jette les (k-1) autres
    return None


class LoopGuard:
    """Throttle autour de :func:`degenerate_cut` pour rester peu coûteux pendant
    le streaming : ne scanne que tous les ``step`` caractères ajoutés, une fois
    la taille minimale (``reps × min_unit``) atteinte.
    """

    def __init__(
        self,
        *,
        reps: int = 4,
        min_unit: int = 16,
        window: int = 2000,
        step: int = 64,
    ) -> None:
        self.reps = reps
        self.min_unit = min_unit
        self.window = window
        self.step = step
        self._next = max(reps * min_unit, step)

    def cut(self, text: str) -> int | None:
        """Renvoie l'index de coupe si la queue de ``text`` est dégénérée, sinon
        ``None``. Bornée par le throttle : appelable à chaque token sans surcoût."""
        if len(text) < self._next:
            return None
        self._next = len(text) + self.step
        return degenerate_cut(
            text, reps=self.reps, min_unit=self.min_unit, window=self.window
        )
