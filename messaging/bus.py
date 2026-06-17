"""M3a - Message Bus (data transport layer).

Carries tag snapshots from the plant/PLC to the dashboard and AI assistant.
Two interchangeable backends are provided:

* ``InProcessBus`` - a simple thread-safe publish/subscribe bus that works with
  zero setup. This is the default and is what the live demo uses.
* ``MqttBus`` - a paho-mqtt backed bus that publishes/subscribes JSON over an
  MQTT broker (e.g. Mosquitto). Use this to demonstrate the "real" MQTT tag
  stream described in the proposal. It is only used if you start a broker and
  set ``use_mqtt=True``.

Both backends expose the same tiny interface::

    bus.publish(topic, payload_dict)
    bus.subscribe(topic, callback)   # callback(payload_dict)
    bus.last(topic)                  # most recent payload on a topic (or None)
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import config


class MessageBus:
    """Base interface. Concrete backends override publish/subscribe."""

    def publish(self, topic: str, payload: Dict) -> None:  # pragma: no cover
        raise NotImplementedError

    def subscribe(self, topic: str, callback: Callable[[Dict], None]) -> None:  # pragma: no cover
        raise NotImplementedError

    def last(self, topic: str) -> Optional[Dict]:  # pragma: no cover
        raise NotImplementedError

    def seconds_since_last(self, topic: str) -> float:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


class InProcessBus(MessageBus):
    """Thread-safe in-memory pub/sub. No external broker required."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[Dict], None]]] = {}
        self._last: Dict[str, Dict] = {}
        self._last_ts: Dict[str, float] = {}
        self._lock = threading.RLock()

    def publish(self, topic: str, payload: Dict) -> None:
        with self._lock:
            self._last[topic] = dict(payload)
            self._last_ts[topic] = time.time()
            callbacks = list(self._subs.get(topic, []))
        for cb in callbacks:
            cb(dict(payload))

    def subscribe(self, topic: str, callback: Callable[[Dict], None]) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)

    def last(self, topic: str) -> Optional[Dict]:
        with self._lock:
            value = self._last.get(topic)
            return dict(value) if value is not None else None

    def seconds_since_last(self, topic: str) -> float:
        with self._lock:
            ts = self._last_ts.get(topic)
        if ts is None:
            return float("inf")
        return time.time() - ts


class MqttBus(MessageBus):
    """MQTT backend using paho-mqtt. Requires a running broker."""

    def __init__(self, host: str = config.MQTT_HOST, port: int = config.MQTT_PORT) -> None:
        try:
            import paho.mqtt.client as mqtt  # noqa: WPS433 (lazy import)
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "paho-mqtt is not installed. Run 'pip install paho-mqtt' or use "
                "the in-process bus (use_mqtt=False)."
            ) from exc

        self._mqtt = mqtt
        self._client = mqtt.Client()
        self._subs: Dict[str, List[Callable[[Dict], None]]] = {}
        self._last: Dict[str, Dict] = {}
        self._last_ts: Dict[str, float] = {}
        self._lock = threading.RLock()

        self._client.on_message = self._on_message
        self._client.connect(host, port, keepalive=30)
        self._client.loop_start()

    def _on_message(self, client, userdata, msg) -> None:  # noqa: D401
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        with self._lock:
            self._last[msg.topic] = payload
            self._last_ts[msg.topic] = time.time()
            callbacks = list(self._subs.get(msg.topic, []))
        for cb in callbacks:
            cb(dict(payload))

    def publish(self, topic: str, payload: Dict) -> None:
        self._client.publish(topic, json.dumps(payload))

    def subscribe(self, topic: str, callback: Callable[[Dict], None]) -> None:
        with self._lock:
            new_topic = topic not in self._subs
            self._subs.setdefault(topic, []).append(callback)
        if new_topic:
            self._client.subscribe(topic)

    def last(self, topic: str) -> Optional[Dict]:
        with self._lock:
            value = self._last.get(topic)
            return dict(value) if value is not None else None

    def seconds_since_last(self, topic: str) -> float:
        with self._lock:
            ts = self._last_ts.get(topic)
        if ts is None:
            return float("inf")
        return time.time() - ts

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def create_bus(use_mqtt: bool = False) -> MessageBus:
    """Factory: return an MQTT bus if requested and reachable, else in-process.

    Falling back to the in-process bus keeps the demo running even if no broker
    is available, which is important on student laptops.
    """
    if use_mqtt:
        try:
            return MqttBus()
        except Exception as exc:  # noqa: BLE001 - any connection failure -> fallback
            print(f"[MessageBus] MQTT unavailable ({exc}); using in-process bus.")
    return InProcessBus()
