"""10 tâches medium — multi-fichier ou refactor léger, < 2min attendus.

Stubs déclarés (id + prompt). À implémenter au fur et à mesure :
chaque stub lève NotImplementedError dans setup/validate, donc tant que
la tâche n'est pas finalisée, --category medium ne l'inclura pas par défaut
(le runner skip via try/except dans _run_one → erreur de setup).
"""
from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


class _Stub(Task):
    """Base pour stubs : signalent clairement qu'ils ne sont pas prêts."""

    def setup(self, workdir: Path) -> None:
        raise NotImplementedError(f"Task {self.id} pas encore implémentée")

    def validate(self, workdir: Path) -> tuple[bool, str]:
        return False, "stub"


@register
class ExtractFunction(_Stub):
    id = "medium/extract_function"
    category = "medium"
    prompt = "TODO: extraire un bloc dupliqué en fonction réutilisable."


@register
class LoopToComprehension(_Stub):
    id = "medium/convert_loop_to_comprehension"
    category = "medium"
    prompt = "TODO: convertir une boucle for/append en list-comprehension."


@register
class AddTypeHints(_Stub):
    id = "medium/add_type_hints"
    category = "medium"
    prompt = "TODO: ajouter des annotations de type à un module."


@register
class FixFailingTest(_Stub):
    id = "medium/fix_failing_test"
    category = "medium"
    prompt = "TODO: un test pytest échoue, corriger le code (pas le test)."


@register
class AddCliArg(_Stub):
    id = "medium/add_cli_arg"
    category = "medium"
    prompt = "TODO: ajouter une option --verbose à un script argparse."


@register
class JsonToDataclass(_Stub):
    id = "medium/json_to_dataclass"
    category = "medium"
    prompt = "TODO: convertir un dict en dataclass typée."


@register
class SplitModule(_Stub):
    id = "medium/split_module"
    category = "medium"
    prompt = "TODO: séparer un gros fichier en 2 modules cohérents."


@register
class AddLogging(_Stub):
    id = "medium/add_logging"
    category = "medium"
    prompt = "TODO: ajouter logging structuré à un script."


@register
class MigratePrintToLogger(_Stub):
    id = "medium/migrate_print_to_logger"
    category = "medium"
    prompt = "TODO: remplacer tous les print par logger.info."


@register
class AddErrorHandling(_Stub):
    id = "medium/add_error_handling"
    category = "medium"
    prompt = "TODO: ajouter try/except contextualisés à un script."
