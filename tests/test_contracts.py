"""Design by Contract (agent/dbc.py) — vérifie que les contrats *mordent*.

Un contrat qui ne peut jamais échouer ne vaut rien : on teste les deux côtés
(violation → lève ; cas valide → passe) sur les primitives et sur les
préconditions/postconditions/invariants posés dans le cœur.
"""

import pytest

from agent.dbc import ContractViolation, ensure, invariant, require


class TestPrimitives:
    def test_require_leve_si_faux(self):
        with pytest.raises(ContractViolation, match="Précondition"):
            require(False, "doit être vrai")

    def test_require_passe_si_vrai(self):
        require(True, "ok")  # ne lève pas

    def test_ensure_leve_si_faux(self):
        with pytest.raises(ContractViolation, match="Postcondition"):
            ensure(False, "garantie cassée")

    def test_invariant_leve_si_faux(self):
        with pytest.raises(ContractViolation, match="Invariant"):
            invariant(False, "état incohérent")

    def test_contract_violation_est_assertion_error(self):
        # Le filet générique de l'orchestrateur attrape Exception → présenté en ERREUR.
        assert issubclass(ContractViolation, AssertionError)


class TestSandboxContracts:
    def test_run_refuse_timeout_non_positif(self):
        from tools.sandbox import SandboxRunner

        runner = SandboxRunner(workdir="/tmp")
        with pytest.raises(ContractViolation, match="timeout"):
            runner.run(["python", "-c", "print(1)"], timeout=0)
        with pytest.raises(ContractViolation, match="timeout"):
            runner.run(["echo", "x"], timeout=-5)


class TestBestOfNContracts:
    def test_init_exige_au_moins_un_candidat(self):
        from agent.best_of_n import BestOfN

        with pytest.raises(ContractViolation, match="au moins 1 candidat"):
            BestOfN(llm_client=object(), n=0)

    def test_init_accepte_n_valide(self):
        from agent.best_of_n import BestOfN

        bon = BestOfN(llm_client=object(), n=3)
        assert bon.n == 3


class TestFileManagerPostcondition:
    def test_write_file_postcondition_tenue(self, tmp_path, monkeypatch):
        # On élargit les racines autorisées à tmp_path pour écrire sous sandbox.
        import config
        from tools.file_manager import FileManager

        monkeypatch.setattr(config, "ALLOWED_ROOTS", [tmp_path])
        fm = FileManager(root=tmp_path, allowed_roots=[tmp_path])
        out = fm.write_file("sub/hello.py", "print('hi')\n")
        assert "succès" in out
        assert (tmp_path / "sub" / "hello.py").exists()  # postcondition vérifiable


class TestMemoryInvariant:
    def _mem(self):
        from agent.memory import ConversationMemory

        return ConversationMemory(session_id="contract-test")

    def test_sliding_window_preserve_l_appariement(self):
        # 30 cycles (assistant tool_call + tool result) → dépasse MAX_MESSAGES et
        # déclenche la fenêtre glissante. L'invariant doit tenir (pas d'orphelin).
        mem = self._mem()
        for i in range(60):
            cid = f"c{i}"
            mem.add_tool_call_message([{
                "id": cid, "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }])
            mem.add_tool_result(cid, "read_file", f"contenu {i}")
        assert mem._orphan_tool_results() == []

    def test_drop_orphan_tool_results_assainit(self):
        mem = self._mem()
        # Injecte directement un tool result orphelin (session héritée corrompue).
        mem.messages.append({
            "role": "tool", "tool_call_id": "inexistant",
            "name": "x", "content": "orphelin",
        })
        assert len(mem._orphan_tool_results()) == 1
        dropped = mem._drop_orphan_tool_results()
        assert dropped == 1
        assert mem._orphan_tool_results() == []

    def test_load_from_file_purge_les_orphelins(self, tmp_path, monkeypatch):
        import json

        import config
        from agent.memory import ConversationMemory

        monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)
        # Session sur disque avec un tool result orphelin + un appariement valide.
        data = {
            "session_id": "legacy",
            "title": "t",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "messages": [
                {"role": "assistant", "tool_calls": [
                    {"id": "ok", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "ok", "name": "f", "content": "valide"},
                {"role": "tool", "tool_call_id": "ghost", "name": "f", "content": "orphelin"},
            ],
        }
        p = tmp_path / "memory_legacy.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        mem = ConversationMemory.load_from_file(p)
        assert mem._orphan_tool_results() == []
        # Le tool result valide est conservé, l'orphelin retiré.
        tool_msgs = [m for m in mem.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "valide"
