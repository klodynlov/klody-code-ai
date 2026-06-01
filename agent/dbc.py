"""Design by Contract léger (Bertrand Meyer, *OOSC*) — sans dépendance.

Trois primitives, dans le vocabulaire d'Eiffel :

- `require(cond, msg)` : **précondition** — ce que la méthode exige de l'appelant.
- `ensure(cond, msg)`  : **postcondition** — ce que la méthode garantit en retour.
- `invariant(cond, msg)` : **invariant de classe** — propriété qui doit tenir
  avant et après chaque opération publique.

Pourquoi pas `icontract` ? Cette lib reconstruit ses messages d'erreur via
`inspect.findsource()` sur le lambda de condition — introspection qui échoue
dans les contextes sans source récupérable (`OSError: could not get source
code`). Klody tourne en LaunchAgent / sous mlx / via divers points d'entrée :
on veut des contrats *robustes*, pas une magie d'introspection. Ici les
conditions sont des booléens ordinaires et les messages sont fournis
explicitement — zéro introspection, comportement identique partout.

Une violation lève `ContractViolation` (sous-classe d'`AssertionError`) : une
**erreur de programmation**, pas une condition d'exécution attendue. À ce titre
elle remonte la pile ; elle n'est pas censée être rattrapée (sauf par le filet
générique de l'orchestrateur, qui la présentera comme une ERREUR).
"""
from __future__ import annotations


class ContractViolation(AssertionError):
    """Rupture de contrat : précondition, postcondition ou invariant non tenu."""


def require(condition: bool, message: str) -> None:
    """Précondition — obligation de l'appelant. Lève si `condition` est fausse."""
    if not condition:
        raise ContractViolation(f"Précondition violée : {message}")


def ensure(condition: bool, message: str) -> None:
    """Postcondition — garantie de la méthode. Lève si `condition` est fausse."""
    if not condition:
        raise ContractViolation(f"Postcondition violée : {message}")


def invariant(condition: bool, message: str) -> None:
    """Invariant de classe — propriété stable. Lève si `condition` est fausse."""
    if not condition:
        raise ContractViolation(f"Invariant violé : {message}")
