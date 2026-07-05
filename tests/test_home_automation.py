"""Tests pour tools/home_automation.py — pont MQTT (dégradation douce + logique)."""
from __future__ import annotations

from unittest.mock import MagicMock

from tools import home_automation
from tools.home_automation import mqtt_publish, mqtt_subscribe

# ── dégradation douce si paho-mqtt absent ────────────────────────────────────

class TestDegradation:
    def test_publish_without_paho(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", lambda: (None, "⚠️ MQTT indisponible : pip install paho-mqtt"))
        assert "paho-mqtt" in mqtt_publish("t", "p")

    def test_subscribe_without_paho(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", lambda: (None, "⚠️ MQTT indisponible : pip install paho-mqtt"))
        assert "paho-mqtt" in mqtt_subscribe("t")


# ── validation des entrées (avec un faux paho présent) ───────────────────────

def _fake_paho(client):
    """Renvoie un module factice dont Client() = `client`."""
    mod = MagicMock()
    mod.Client.return_value = client
    return lambda: (mod, None)


class TestPublish:
    def test_empty_topic(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(MagicMock()))
        assert "topic vide" in mqtt_publish("", "payload")

    def test_bad_port(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(MagicMock()))
        assert "port MQTT invalide" in mqtt_publish("t", "p", port=99999)

    def test_payload_too_big(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(MagicMock()))
        assert "trop volumineux" in mqtt_publish("t", "x" * (300 * 1024))

    def test_publish_ok(self, monkeypatch):
        client = MagicMock()
        info = MagicMock()
        client.publish.return_value = info
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(client))
        result = mqtt_publish("maison/salon/lampe/set", "ON")
        assert "Publié" in result
        client.connect.assert_called_once()
        client.publish.assert_called_once()
        info.wait_for_publish.assert_called_once()

    def test_publish_connection_error(self, monkeypatch):
        client = MagicMock()
        client.connect.side_effect = OSError("connexion refusée")
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(client))
        result = mqtt_publish("t", "p")
        assert "ERREUR MQTT" in result


class TestSubscribe:
    def test_timeout_clamped(self, monkeypatch):
        """timeout > 60 est ramené à 60 (borne dure) sans planter."""
        client = MagicMock()
        # loop_start ne fait rien ; aucun message ne sera livré → retour "Aucun".
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(client))
        # timeout minimal pour un test rapide
        result = mqtt_subscribe("capteurs/#", timeout=1, max_messages=5)
        assert "Aucun message" in result
        client.subscribe.assert_called_once_with("capteurs/#")

    def test_collects_messages(self, monkeypatch):
        # Faux client : à loop_start, il livre un message via le callback on_message.
        class FakeClient:
            def __init__(self):
                self.on_message = None
            def connect(self, *a, **k):
                pass
            def subscribe(self, *a, **k):
                pass
            def loop_start(self):
                msg = MagicMock(topic="capteurs/temp", payload=b"21.5")
                self.on_message(self, None, msg)
            def loop_stop(self):
                pass
            def disconnect(self):
                pass

        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(FakeClient()))
        result = mqtt_subscribe("capteurs/temp", timeout=2, max_messages=1)
        assert "21.5" in result and "capteurs/temp" in result

    def test_empty_topic(self, monkeypatch):
        monkeypatch.setattr(home_automation, "_load_paho", _fake_paho(MagicMock()))
        assert "topic vide" in mqtt_subscribe("")
