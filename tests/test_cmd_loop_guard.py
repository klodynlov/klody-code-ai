"""Anti-boucle COMPORTEMENTALE cross-run (commande shell qui rate à l'identique).

Angle mort réel du 08/07 : le modèle relançait sans fin `python main.py` (lancé
depuis la mauvaise racine → « No such file »). Aucun garde ne coupait car :
  - `call_repeat_counts` est reset à CHAQUE message (l'orchestrator est reconstruit
    par message WS) → il ne voyait jamais la répétition cross-run ;
  - le text-to-action fallback exécute SANS l'alimenter.

Le compteur vit désormais sur la MÉMOIRE de session (persistante entre messages).
On teste la brique de décision sans LLM ni boucle ReAct réelle.
"""
from types import SimpleNamespace

from agent.memory import ConversationMemory
from agent.orchestrator import (
    _CMD_FAIL_STREAK_BREAK,
    Orchestrator,
    _cmd_result_failed,
)

# Résultats typiques du terminal (cf. tools/terminal.py).
FAIL = "python: can't open file 'main.py': [Errno 2] No such file\n[Code de retour: 2]"
FAIL_ERR = "ERREUR: Timeout après 30 secondes."
OK = "Hello world\n(aucune sortie)"


def _orch():
    """Orchestrator minimal : on court-circuite __init__ (lourd : LLM, MCP…) et
    on ne pose que ce dont la brique cross-run a besoin."""
    o = Orchestrator.__new__(Orchestrator)
    o.memory = ConversationMemory()
    o.terminal = SimpleNamespace(cwd="/Users/klodynlov/Projets")
    return o


class TestCmdResultFailed:
    def test_code_retour_non_zero(self):
        assert _cmd_result_failed("blah\n[Code de retour: 2]") is True

    def test_erreur_prefix(self):
        assert _cmd_result_failed("ERREUR: Timeout après 30 secondes.") is True
        assert _cmd_result_failed("ERREUR SÉCURITÉ: sudo interdit") is True

    def test_succes_non_echec(self):
        assert _cmd_result_failed("Hello world") is False
        assert _cmd_result_failed("(aucune sortie)") is False

    def test_vide(self):
        assert _cmd_result_failed("") is False


class TestNoteCmdOutcome:
    def test_echecs_consecutifs_incrementent(self):
        o = _orch()
        args = {"command": "python main.py"}
        assert o._note_cmd_outcome("execute_command", args, FAIL) == 1
        assert o._note_cmd_outcome("execute_command", args, FAIL) == 2
        assert o._note_cmd_outcome("execute_command", args, FAIL) == 3

    def test_seuil_de_coupe_au_2e_echec(self):
        o = _orch()
        args = {"command": "python main.py"}
        s1 = o._note_cmd_outcome("execute_command", args, FAIL)
        s2 = o._note_cmd_outcome("execute_command", args, FAIL)
        assert s1 < _CMD_FAIL_STREAK_BREAK      # 1er échec : on laisse une chance
        assert s2 >= _CMD_FAIL_STREAK_BREAK      # 2e échec identique : coupe

    def test_succes_rompt_la_boucle(self):
        o = _orch()
        args = {"command": "python main.py"}
        o._note_cmd_outcome("execute_command", args, FAIL)      # 1
        assert o._note_cmd_outcome("execute_command", args, OK) == 0
        # Après un succès, un nouvel échec repart de zéro (pas de faux positif).
        assert o._note_cmd_outcome("execute_command", args, FAIL) == 1

    def test_erreur_qui_evolue_non_comptee(self):
        """Même commande mais sortie DIFFÉRENTE (l'erreur change = progrès) ne
        doit PAS être vue comme une boucle — c'est le discriminateur clé vs un
        échec figé. La signature inclut le résultat (comme call_repeat_counts)."""
        o = _orch()
        args = {"command": "pytest"}
        fail_a = "FAILED test_x - AssertionError\n[Code de retour: 1]"
        fail_b = "FAILED test_y - KeyError\n[Code de retour: 1]"
        assert o._note_cmd_outcome("execute_command", args, fail_a) == 1
        assert o._note_cmd_outcome("execute_command", args, fail_b) == 1  # sortie ≠ → repart
        assert o._note_cmd_outcome("execute_command", args, fail_a) == 2  # MÊME échec → monte

    def test_erreur_timeout_compte_comme_echec(self):
        o = _orch()
        args = {"command": "sleep 999"}
        assert o._note_cmd_outcome("execute_command", args, FAIL_ERR) == 1
        assert o._note_cmd_outcome("execute_command", args, FAIL_ERR) == 2

    def test_outil_non_commande_neutre(self):
        o = _orch()
        # read_file qui « rend le même contenu » n'est PAS une boucle d'échec.
        assert o._note_cmd_outcome("read_file", {"path": "x.py"}, FAIL) == 0
        assert o.memory.cmd_failure_streak == {}

    def test_commandes_differentes_suivies_separement(self):
        o = _orch()
        o._note_cmd_outcome("execute_command", {"command": "a"}, FAIL)   # a:1
        o._note_cmd_outcome("execute_command", {"command": "b"}, FAIL)   # b:1
        # La 2e occurrence de `a` atteint le seuil même si `b` est passée entre.
        assert o._note_cmd_outcome("execute_command", {"command": "a"}, FAIL) == 2

    def test_args_ordre_indifferent(self):
        o = _orch()
        a1 = {"command": "x", "reason": "r"}
        a2 = {"reason": "r", "command": "x"}   # mêmes clés, ordre inverse
        assert o._note_cmd_outcome("execute_command", a1, FAIL) == 1
        assert o._note_cmd_outcome("execute_command", a2, FAIL) == 2

    def test_reason_variable_ne_defait_pas_le_garde(self):
        """RÉGRESSION (trou vu en live 08/07) : le modèle fait varier le champ
        libre `reason` à chaque relance (« n°1 », « n°2 »…). La signature doit
        clé sur la COMMANDE seule, sinon chaque appel a une signature neuve et le
        garde ne coupe jamais (4 `cat` identiques étaient passés)."""
        o = _orch()
        cmd = "cat __klody_ghost_42__.txt"
        a1 = {"command": cmd, "reason": "L'utilisateur demande d'exécuter"}
        a2 = {"command": cmd, "reason": "Relance identique n°1"}
        a3 = {"command": cmd, "reason": "Relance identique n°2"}
        assert o._note_cmd_outcome("execute_command", a1, FAIL) == 1
        assert o._note_cmd_outcome("execute_command", a2, FAIL) == 2  # reason ≠ mais cmd = → monte
        assert o._note_cmd_outcome("execute_command", a3, FAIL) == 3

    def test_borne_defensive_taille(self):
        o = _orch()
        for i in range(40):   # 40 échecs TOUS différents
            o._note_cmd_outcome("execute_command", {"command": f"c{i}"}, FAIL)
        assert len(o.memory.cmd_failure_streak) <= 32


class TestCmdLoopNudge:
    def test_nudge_injecte_role_user_et_persistant(self):
        o = _orch()
        n0 = len(o.memory.messages)
        o._cmd_loop_nudge({"command": "python main.py"}, 3)
        assert len(o.memory.messages) == n0 + 1
        msg = o.memory.messages[-1]
        assert msg["role"] == "user"
        assert "STOP" in msg["content"]
        assert "python main.py" in msg["content"]
        # Le nudge cite le CWD réel → oriente la correction (gotcha racine sandbox).
        assert str(o.terminal.cwd) in msg["content"]

    def test_nudge_tolere_args_sans_command(self):
        o = _orch()
        o._cmd_loop_nudge({}, 2)   # ne doit pas lever
        assert o.memory.messages[-1]["role"] == "user"


class TestPersistanceCrossMessage:
    def test_le_compteur_survit_a_un_nouvel_orchestrator(self):
        """Cœur du fix : deux orchestrators DIFFÉRENTS (comme deux messages WS)
        partageant la MÊME mémoire voient le même streak."""
        memory = ConversationMemory()
        args = {"command": "python main.py"}

        o1 = Orchestrator.__new__(Orchestrator)
        o1.memory = memory
        o1.terminal = SimpleNamespace(cwd="/Users/klodynlov/Projets")
        assert o1._note_cmd_outcome("execute_command", args, FAIL) == 1

        # Nouveau message WS = nouvel orchestrator, même mémoire de session.
        o2 = Orchestrator.__new__(Orchestrator)
        o2.memory = memory
        o2.terminal = SimpleNamespace(cwd="/Users/klodynlov/Projets")
        assert o2._note_cmd_outcome("execute_command", args, FAIL) == 2  # >= seuil
