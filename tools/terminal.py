import logging
import re
import shlex
import subprocess

from config import PROJECT_ROOT, SUBPROCESS_TIMEOUT
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

logger = logging.getLogger(__name__)
console = Console()

# Sous-chaînes dont la présence (insensible à la casse) suffit à bloquer
_BLOCKED_SUBSTRINGS: list[str] = [
    # Destruction disque / filesystem
    "rm -rf /",
    "chmod 777",
    ":(){ :|:& };:",
    "> /dev/sda",
    "> /dev/hda",
    "> /dev/sdb",
    "mkfs",
    "dd if=",
    # Download + exec
    "curl|bash",
    "curl | bash",
    "wget|sh",
    "wget | sh",
    "|bash",
    "| bash",
    "|sh",
    "| sh",
    # Shell inception (exécution de sous-shell arbitraire)
    "bash -c ",
    "sh -c ",
    "zsh -c ",
    "fish -c ",
    "dash -c ",
    # NB : les one-liners d'interpréteur (python -c, python3 -c, ruby/perl -e,
    # node -e/--eval, php -r) ne sont VOLONTAIREMENT pas bloqués. Klody est un
    # outil local/offline mono-utilisateur où l'agent peut déjà exécuter du code
    # arbitraire via `python fichier.py`, `python -m …`, etc. — les bloquer
    # n'ajoutait aucune sécurité réelle, juste des faux positifs récurrents
    # (l'agent s'en servait légitimement pour inspecter du JSON). Ne pas réajouter.
    # Fuite de variables d'environnement (tokens, clés API)
    "printenv",
    # Outils réseau offensifs
    "nc -",
    "ncat -",
    "netcat -",
    # Téléchargement + exécution chaîné
    "&& bash",
    "&& sh",
    "; bash ",
    "; sh ",
    # Lecture de fichiers sensibles hors sandbox
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "~/.ssh/",
    "~/.aws/",
    "~/.gnupg/",
    "id_rsa",
    "id_ed25519",
]

# Regex complémentaires
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r">\s*/dev/hd[a-z]"),
    re.compile(r"dd\s+if=/dev"),
    re.compile(r"mkfs\.\w+"),
    # Commande `env` seule ou avec args (fuite secrets)
    re.compile(r"^\s*env\b"),
    re.compile(r";\s*env\b"),
    re.compile(r"&&\s*env\b"),
    # Écriture dans /tmp puis exécution
    re.compile(r">\s*/tmp/.*&&"),
    re.compile(r">\s*/tmp/.*;\s*(bash|sh|python|node)"),
    # Lecture de répertoires sensibles système
    re.compile(r"\bcat\s+/etc/\w"),
    re.compile(r"\bcat\s+~/?\.(ssh|aws|gnupg|config)/"),
    re.compile(r"\bless\s+/etc/(passwd|shadow|sudoers)"),
    re.compile(r"\bmore\s+/etc/(passwd|shadow|sudoers)"),
    # Download + exec chaîné sans pipe
    re.compile(r"\b(curl|wget)\b.*&&\s*(bash|sh|python|node|exec)"),
]

# Taille maximale de la sortie d'une commande (caractères)
MAX_OUTPUT_SIZE = 50_000

# Marqueurs « fichier introuvable » (toutes casses) — cause n°1 du gotcha racine
# sandbox : une commande lancée depuis PROJECT_ROOT vise un fichier qui est en
# fait dans un sous-dossier (`python main.py` alors que main.py est ailleurs).
_MISSING_FILE_MARKERS: tuple[str, ...] = (
    "no such file or directory",
    "can't open file",
    "cannot find the file",
    "aucun fichier de ce type",
)


def _looks_like_missing_file(text: str) -> bool:
    """Vrai si la sortie dénote un fichier/chemin introuvable."""
    low = (text or "").lower()
    return any(marker in low for marker in _MISSING_FILE_MARKERS)


# Marqueurs de traceback Python / erreur de script dans une sortie.
_TRACEBACK_MARKERS: tuple[str, ...] = (
    "traceback (most recent call last)",
    "error: python:",  # forme Blender ("Error: Python: Traceback…")
)

# Hôtes qui EXÉCUTENT un script mais AVALENT le code de sortie : le script lève,
# l'hôte imprime le traceback… puis quitte avec le code 0. Sans garde, le terminal
# rapporte « Succès (code 0) », l'orchestrator NE compte pas d'échec (cf.
# `_cmd_result_failed`) et le modèle reboucle en croyant avoir réussi (constaté en
# live 09/07 : Blender 5.x, script de visage 3D relancé ~8× sur exit 0 trompeur).
# Cas prouvé = Blender `--background --python`. Le correctif propre côté commande
# est `--python-exit-code 1` (le modèle doit l'ajouter ; cf. skill blender_python_bpy).
_EXITCODE_SWALLOWING_HOSTS: tuple[str, ...] = ("blender",)


def _looks_like_script_error(text: str) -> bool:
    """Vrai si la sortie contient un traceback Python / erreur de script."""
    low = (text or "").lower()
    return any(marker in low for marker in _TRACEBACK_MARKERS)


def _host_swallows_exit_code(command: str) -> bool:
    """Vrai si la commande invoque un hôte connu pour rendre exit 0 même quand
    le script qu'il exécute lève (Blender `--python`/`-P`)."""
    low = (command or "").lower()
    if not any(host in low for host in _EXITCODE_SWALLOWING_HOSTS):
        return False
    return "--python" in low or " -p " in f" {low} "


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

        # Auto-confirm en mode non-interactif (API/WS, scripts) — stdin n'a pas de TTY.
        # Le check de sécurité au-dessus a déjà bloqué les commandes dangereuses,
        # on peut donc auto-accepter ici. Sinon : prompt classique.
        import sys
        if not sys.stdin.isatty():
            logger.info("Auto-confirm (non-TTY): %s", command)
            confirmed = True
        else:
            try:
                confirmed = Confirm.ask("[yellow]Exécuter cette commande ?[/yellow]", default=False)
            except (EOFError, KeyboardInterrupt):
                logger.warning("Confirmation impossible (EOF) — commande refusée par défaut: %s", command)
                return "Commande refusée (confirmation interactive impossible)."

        if not confirmed:
            logger.info("Commande refusée par l'utilisateur: %s", command)
            return "Commande refusée par l'utilisateur."

        # Commande détachée (se termine par un `&` isolé, pas `&&`) : la lancer
        # SANS capturer ses pipes. Sinon le process serveur backgroundé hérite
        # des descripteurs de capture et subprocess.run bloque jusqu'au timeout
        # en attendant un EOF qui n'arrive jamais (cf. sessions : lancement du
        # proxy RAG `… &` → Timeout 30s systématique, Klody ne pouvait jamais
        # démarrer un service en arrière-plan).
        stripped = command.rstrip()
        if stripped.endswith("&") and not stripped.endswith("&&"):
            return self._run_detached(command)

        logger.info("Exécution: %s", command)

        try:
            # shell=True voulu: l'outil expose un terminal au LLM (par design).
            # Confirmation + allowlist orchestrator + sandbox venv encadrent l'usage.
            result = subprocess.run(  # nosec B602
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

            output = "\n".join(parts).strip() or "(aucune sortie)"
            if len(output) > MAX_OUTPUT_SIZE:
                output = output[:MAX_OUTPUT_SIZE] + f"\n… [sortie tronquée — {len(output) - MAX_OUTPUT_SIZE} chars supplémentaires]"
            # Gotcha racine sandbox : `python main.py` lancé depuis PROJECT_ROOT
            # alors que le fichier est dans un sous-dossier → « No such file ».
            # On enrichit l'échec d'un indice ACTIONNABLE (CWD réel + pistes) pour
            # que le modèle corrige DÈS le 1er échec au lieu de reboucler. Détection
            # sur stderr (non tronqué) → robuste même si stdout est coupé.
            if result.returncode != 0 and _looks_like_missing_file(result.stderr or output):
                output += (
                    f"\n[Indice CWD] Commande exécutée depuis « {self.cwd} ». "
                    "Le fichier visé n'existe pas ici — il est probablement dans "
                    "un sous-dossier. Utilise un chemin ABSOLU, `cd <dossier> && …`, "
                    "ou crée d'abord le fichier avec write_file avant de le lancer."
                )
            # Échec AVALÉ : exit 0 mais traceback dans la sortie (Blender & co).
            # On requalifie en échec — le token « [Code de retour: 1 » réarme
            # `_cmd_result_failed` (anti-boucle) ET dit clairement au modèle de NE
            # PAS considérer la commande comme réussie.
            if result.returncode == 0 and _host_swallows_exit_code(command) \
                    and _looks_like_script_error(output):
                logger.warning(
                    "Échec avalé (exit 0 + traceback) requalifié en échec: %s", command)
                output += (
                    "\n[Code de retour: 1 — ÉCHEC détecté] Traceback Python dans la "
                    "sortie MALGRÉ un code de sortie 0 : l'hôte (Blender) avale "
                    "l'erreur. NE considère PAS cette commande comme réussie — le "
                    "script a planté. Corrige la cause ci-dessus, et relance Blender "
                    "avec `--python-exit-code 1` pour obtenir un vrai code d'échec."
                )
            return output

        except subprocess.TimeoutExpired:
            logger.error("Timeout (%ds): %s", SUBPROCESS_TIMEOUT, command)
            return f"ERREUR: Timeout après {SUBPROCESS_TIMEOUT} secondes."
        except Exception as e:
            logger.error("Erreur subprocess '%s': %s", command, e)
            return f"ERREUR: {e}"

    def _run_detached(self, command: str) -> str:
        """Lance une commande backgroundée (`… &`) détachée, sans bloquer.

        Pipes branchés sur /dev/null (la redirection éventuelle de la commande,
        ex. `> log 2>&1`, reste appliquée par le shell), nouvelle session
        (start_new_session) pour survivre à la fin du tour. On ne wait/communicate
        PAS : la fonction rend la main immédiatement."""
        try:
            proc = subprocess.Popen(  # nosec B602
                command,
                shell=True,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logger.error("Erreur lancement détaché '%s': %s", command, e)
            return f"ERREUR: {e}"
        logger.info("Lancé en arrière-plan (PID %d): %s", proc.pid, command)
        return (
            f"Lancé en arrière-plan (PID {proc.pid}). La commande tourne détachée ; "
            "vérifie son état avec une commande de poll (ex. `curl … /health`, `pgrep`, "
            "ou lis son fichier de log si tu en as redirigé la sortie)."
        )
