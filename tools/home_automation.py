"""Maison connectée & IoT — pont MQTT.

MQTT est le dénominateur commun de l'écosystème domotique local : un ESP32, un
Raspberry Pi, un broker Mosquitto, un pont HomeKit (Homebridge/Home Assistant)
parlent tous MQTT. Klody publie une commande ou écoute un topic, sans dépendre
d'un cloud propriétaire.

Dépendance **optionnelle** `paho-mqtt` : absente, les outils rendent un message
d'installation clair (dégradation douce, jamais d'exception) — même posture que
les briques optionnelles du projet (sqlite-vec, klody-memory…).

Sûreté :
- Hôte/port validés ; par défaut le broker local (`127.0.0.1:1883`).
- `mqtt_subscribe` est **borné** (timeout + nombre max de messages) : il ne peut
  pas bloquer la boucle ReAct indéfiniment.
- Payload plafonné (256 Kio) pour éviter d'inonder un topic.
"""
from __future__ import annotations

import logging

from config import MQTT_DEFAULT_HOST, MQTT_DEFAULT_PORT

logger = logging.getLogger(__name__)

_MAX_PAYLOAD = 256 * 1024      # 256 Kio
_MAX_SUB_TIMEOUT = 60          # s — plafond dur d'écoute
_MAX_SUB_MESSAGES = 50         # messages collectés max
_CONNECT_TIMEOUT = 10          # s


def _load_paho():
    """Importe paho-mqtt à la demande, renvoie (module, None) ou (None, message)."""
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except Exception:
        return None, (
            "⚠️ MQTT indisponible : le paquet 'paho-mqtt' n'est pas installé. "
            "Installe-le avec `pip install paho-mqtt` pour piloter tes appareils "
            "(ESP32, Raspberry Pi, Home Assistant…)."
        )
    return mqtt, None


def _valid_broker(host: str, port: int) -> str | None:
    if not host or not host.strip():
        return "ERREUR : hôte du broker vide."
    if not (0 < int(port) < 65536):
        return f"ERREUR : port MQTT invalide : {port}."
    return None


def mqtt_publish(
    topic: str,
    payload: str,
    host: str = "",
    port: int = 0,
    retain: bool = False,
    qos: int = 0,
) -> str:
    """Publie un message sur un topic MQTT (commande un appareil).

    Args:
        topic: topic MQTT (ex. `maison/salon/lampe/set`).
        payload: charge utile (ex. `ON`, `{"state":"on","bri":128}`).
        host: broker (défaut MQTT_DEFAULT_HOST).
        port: port broker (défaut MQTT_DEFAULT_PORT).
        retain: message retenu par le broker (dernière valeur connue).
        qos: qualité de service MQTT (0, 1 ou 2).
    """
    mqtt, err = _load_paho()
    if err:
        return err
    host = host.strip() or MQTT_DEFAULT_HOST
    port = int(port) or MQTT_DEFAULT_PORT
    if (bad := _valid_broker(host, port)):
        return bad
    if not topic or not topic.strip():
        return "ERREUR : topic vide."
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD:
        return f"ERREUR : payload trop volumineux (> {_MAX_PAYLOAD // 1024} Kio)."
    qos = qos if qos in (0, 1, 2) else 0

    client = mqtt.Client()
    try:
        client.connect(host, port, keepalive=_CONNECT_TIMEOUT)
        client.loop_start()
        info = client.publish(topic, payload, qos=qos, retain=bool(retain))
        info.wait_for_publish(timeout=_CONNECT_TIMEOUT)
        client.loop_stop()
        client.disconnect()
    except Exception as e:  # réseau, broker absent, auth…
        logger.warning("MQTT publish échec %s:%s → %s", host, port, e)
        return f"ERREUR MQTT : impossible de publier sur {host}:{port} — {e}"

    logger.info("MQTT publish %s (%d o) → %s:%s", topic, len(payload), host, port)
    return f"✅ Publié sur '{topic}' ({host}:{port}, qos={qos}, retain={retain})."


def mqtt_subscribe(
    topic: str,
    host: str = "",
    port: int = 0,
    timeout: int = 10,
    max_messages: int = 10,
) -> str:
    """Écoute un topic MQTT pendant un temps borné et renvoie les messages reçus.

    Écoute NON bloquante : s'arrête au premier des deux seuils atteint
    (`timeout` secondes OU `max_messages` messages).

    Args:
        topic: topic ou filtre à souscrire (ex. `maison/#`, `capteurs/+/temp`).
        host: broker (défaut MQTT_DEFAULT_HOST).
        port: port broker (défaut MQTT_DEFAULT_PORT).
        timeout: durée d'écoute max en secondes (plafonné à 60).
        max_messages: messages max à collecter (plafonné à 50).
    """
    mqtt, err = _load_paho()
    if err:
        return err
    host = host.strip() or MQTT_DEFAULT_HOST
    port = int(port) or MQTT_DEFAULT_PORT
    if (bad := _valid_broker(host, port)):
        return bad
    if not topic or not topic.strip():
        return "ERREUR : topic vide."

    timeout = max(1, min(int(timeout or 10), _MAX_SUB_TIMEOUT))
    max_messages = max(1, min(int(max_messages or 10), _MAX_SUB_MESSAGES))

    collected: list[str] = []

    def _on_message(_client, _userdata, msg):
        try:
            body = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            body = repr(msg.payload)
        collected.append(f"[{msg.topic}] {body}")
        if len(collected) >= max_messages:
            _client.disconnect()

    client = mqtt.Client()
    client.on_message = _on_message
    try:
        client.connect(host, port, keepalive=_CONNECT_TIMEOUT)
        client.subscribe(topic)
        client.loop_start()
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(collected) < max_messages:
            time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        logger.warning("MQTT subscribe échec %s:%s → %s", host, port, e)
        return f"ERREUR MQTT : impossible d'écouter {host}:{port} — {e}"

    if not collected:
        return (
            f"Aucun message sur '{topic}' en {timeout}s ({host}:{port}). "
            f"L'appareil publie-t-il bien sur ce topic ?"
        )
    header = f"{len(collected)} message(s) reçu(s) sur '{topic}' :"
    return header + "\n" + "\n".join(f"  {m}" for m in collected)
