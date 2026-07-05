"""Tests pour tools/k8s_tools — introspection Kubernetes lecture seule (Roadmap v2 #10).

kubectl et subprocess.run sont mockés (aucun cluster requis). Cœur : anti-injection
de commande (argv sans shell, entrées validées) et refus des mutations.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from tools import k8s_tools
from tools.k8s_tools import format_kubectl_result, kubectl_control


@pytest.fixture
def fake_kubectl(monkeypatch):
    monkeypatch.setattr(k8s_tools.shutil, "which", lambda _: "/usr/bin/kubectl")
    calls: list[list[str]] = []
    result = {"returncode": 0, "stdout": "OK", "stderr": ""}

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs.get("shell") is False
        return SimpleNamespace(returncode=result["returncode"],
                               stdout=result["stdout"], stderr=result["stderr"])

    monkeypatch.setattr(k8s_tools.subprocess, "run", _fake_run)
    return SimpleNamespace(calls=calls, result=result)


class TestActions:
    def test_get_argv(self, fake_kubectl):
        res = kubectl_control("get", resource="pods")
        assert res["ok"] is True
        argv = fake_kubectl.calls[0]
        assert argv[:3] == ["kubectl", "get", "pods"]
        assert "-o" in argv and "wide" in argv
        assert "--request-timeout=10s" in argv

    def test_action_mutante_refusee(self, fake_kubectl):
        for bad in ("delete", "apply", "exec", "scale", "edit"):
            res = kubectl_control(bad, resource="pods")
            assert res["ok"] is False and "non supportée" in res["error"]
        assert fake_kubectl.calls == []

    def test_mutations_absentes_de_lenum(self):
        from tools.registry import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "kubectl_control")
        enum = set(tool["function"]["parameters"]["properties"]["action"]["enum"])
        assert enum.isdisjoint({"apply", "create", "delete", "edit", "scale",
                                "patch", "exec", "cp", "rollout", "drain"})

    def test_namespace_all(self, fake_kubectl):
        kubectl_control("get", resource="pods", namespace="all")
        assert "--all-namespaces" in fake_kubectl.calls[0]

    def test_namespace_precis(self, fake_kubectl):
        kubectl_control("get", resource="pods", namespace="kube-system")
        argv = fake_kubectl.calls[0]
        i = argv.index("-n")
        assert argv[i + 1] == "kube-system"

    def test_top_seulement_pods_nodes(self, fake_kubectl):
        assert kubectl_control("top", resource="deployments")["ok"] is False
        assert kubectl_control("top", resource="pods")["ok"] is True

    def test_logs_tail_clampe_et_container(self, fake_kubectl):
        kubectl_control("logs", name="web-abc", container="app", tail=99999)
        argv = fake_kubectl.calls[0]
        assert argv[argv.index("--tail") + 1] == "500"
        assert argv[argv.index("-c") + 1] == "app"


class TestValidation:
    def test_resource_requise(self, fake_kubectl):
        assert kubectl_control("get")["ok"] is False
        assert fake_kubectl.calls == []

    def test_name_requis_describe(self, fake_kubectl):
        assert kubectl_control("describe", resource="pods")["ok"] is False

    @pytest.mark.parametrize("kwargs", [
        {"action": "get", "resource": "pods; rm -rf /"},
        {"action": "get", "resource": "-o=jsonpath"},
        {"action": "describe", "resource": "pods", "name": "$(whoami)"},
        {"action": "describe", "resource": "pods", "name": "--kubeconfig=/x"},
        {"action": "get", "resource": "pods", "namespace": "ns && id"},
        {"action": "get", "resource": "pods", "namespace": "-n"},
        {"action": "logs", "name": "web", "container": "`id`"},
    ])
    def test_injections_refusees(self, fake_kubectl, kwargs):
        # Chaque cas cible le champ malveillant avec une action qui l'UTILISE
        # réellement (sinon le champ serait ignoré, pas validé).
        res = kubectl_control(**kwargs)
        assert res["ok"] is False
        assert fake_kubectl.calls == []  # jamais passé à subprocess

    @pytest.mark.parametrize("res_name", ["pods", "deployments", "ingresses.networking.k8s.io"])
    def test_resources_valides(self, fake_kubectl, res_name):
        assert kubectl_control("get", resource=res_name)["ok"] is True

    def test_nom_valide_est_un_argv_distinct(self, fake_kubectl):
        kubectl_control("describe", resource="pods", name="web-7d9f-abc")
        argv = fake_kubectl.calls[0]
        assert "web-7d9f-abc" in argv


class TestEnvironnement:
    def test_kubectl_absent(self, monkeypatch):
        monkeypatch.setattr(k8s_tools.shutil, "which", lambda _: None)
        res = kubectl_control("get", resource="pods")
        assert res["ok"] is False and "introuvable" in res["error"]

    def test_cluster_injoignable(self, fake_kubectl):
        fake_kubectl.result.update(returncode=1, stderr="The connection to the server was refused")
        res = kubectl_control("get", resource="pods")
        assert res["ok"] is False and "injoignable" in res["error"]

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr(k8s_tools.shutil, "which", lambda _: "/usr/bin/kubectl")

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="kubectl", timeout=15)

        monkeypatch.setattr(k8s_tools.subprocess, "run", _boom)
        assert kubectl_control("get", resource="pods")["ok"] is False


class TestSortie:
    def test_troncature(self, fake_kubectl):
        fake_kubectl.result.update(stdout="y" * 50_000)
        res = kubectl_control("get", resource="pods")
        assert res["truncated"] is True

    def test_format(self, fake_kubectl):
        out = format_kubectl_result(kubectl_control("version"))
        assert out.startswith("$ kubectl version")

    def test_format_erreur(self):
        assert format_kubectl_result({"ok": False, "error": "boom"}) == "boom"
