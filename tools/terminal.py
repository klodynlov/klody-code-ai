import logging
import re
import shlex
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from config import PROJECT_ROOT, SUBPROCESS_TIMEOUT

logger = logging.getLogger(__name__)
console = Console()

# Sous-chaînes dont la présence (insensible à la casse) suffit à bloquer
_BLOCKED_SUBSTRINGS: list[str] = [
    "rm -rf /",
    "chmod 777",
    ":(){ :|:& };:",
    "> /dev/sda",
    "> /dev/hda",
    "> /dev/sdb",
    "mkfs",
    "dd if=",
    "curl|bash",
    "curl | bash",
    "wget|sh",
    "wget | sh",
    "|bash",
    "| bash",
    "|sh",
    "| sh",
]

# Regex complémentaires
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r">\s*/dev/hd[a-z]"),
    re.compile(r"dd\s+if=/dev"),
    re.compile(r"mkfs\.\w+"),
]


class CommandBlocked(Exception):
    """Commande refusée par la politique de sécurité."""


class Terminal:
    def __init__(self, cwd=PROJECT_ROOT):
        self.cwd = cwd

    def _check_command_safety(self, command: str) -> None:
        """
        Vérifie que la commande ne correspond à aucun pattern dangereux.
        Lève CommandBlocked si interdite.
        """
        cmd_lower = command.lower().strip()

        # sudo en premier token
        try:
            tokens = shlex.split(command)
            if tokens and tokens[0] == "sudo":
                raise CommandBlocked("sudo est interdit sans exception")
        except ValueError:
            pass

        for substring in _BLOCKED_SUBSTRINGS:
            if substring.lower() in cmd_lower:
                raise CommandBlocked(f"Pattern dangereux détecté: '{substring}'")

        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(cmd_lower):
                raise CommandBlocked(f"Pattern regex dangereux: '{pattern.pattern}'")

    def execute_command(self, command: str, reason: str = "") -> str:
        """
        Exécute une commande shell après vérification sécurité et confirmation humaine.
        Défaut de confirmation = NON.
        """
        try:
            self._check_command_safety(command)
        except CommandBlocked as e:
            logger.warning("Commande bloquée: %s | %s", command, e)
            return f"ERREUR SÉCURITÉ: {e}"

        console.print()
        console.print(Panel(
            Syntax(command, "bash", theme="monokai", word_wrap=True),
            title="[yellow]⚡ Commande à exécuter[/yellow]",
            subtitle=f"[dim]{reason}[/dim]" if reason else None,
            border_style="yellow",
            padding=(0, 1),
        ))

        confirmed = Confirm.ask("[yellow]Exécuter cette commande ?[/yellow]", default=False)

        if not confirmed:
            logger.info("Commande refusée par l'utilisateur: %s", command)
            return "Commande refusée par l'utilisateur."

        logger.info("Exécution: %s", command)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=SUBPROCESS_TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )

            parts = []
            if result.stdout:
                parts.append(result.stdout)
            if result.stderr:
                parts.append(f"[STDERR]\n{result.stderr}")
            if result.returncode != 0:
                parts.append(f"[Code de retour: {result.returncode}]")
                logger.warning("Code de retour %d: %s", result.returncode, command)
            else:
                logger.info("Succès (code 0): %s", command)

            return "\n".join(parts).strip() or "(aucune sortie)"

        except subprocess.TimeoutExpired:
            logger.error("Timeout (%ds): %s", SUBPROCESS_TIMEOUT, command)
            return f"ERREUR: Timeout après {SUBPROCESS_TIMEOUT} secondes."
        except Exception as e:
            logger.error("Erreur subprocess '%s': %s", command, e)
            return f"ERREUR: {e}"
