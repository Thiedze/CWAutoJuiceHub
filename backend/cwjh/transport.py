"""MQTT transports.

`PahoTransport` talks to a real broker. `LocalBroker` is an in-process broker for tests and
for running the backend together with the simulator without Mosquitto.
"""

import threading
from collections.abc import Callable
from typing import Protocol

OnMessage = Callable[[str, bytes], None]
OnConnect = Callable[[], None]


class Transport(Protocol):
    def start(self, on_message: OnMessage, subscriptions: list[str], on_connect: OnConnect | None = None) -> None: ...

    def publish(self, topic: str, payload: bytes | str, qos: int = 1, retain: bool = False) -> None: ...

    def stop(self) -> None: ...


def topic_matches(pattern: str, topic: str) -> bool:
    pat = pattern.split("/")
    top = topic.split("/")
    for i, part in enumerate(pat):
        if part == "#":
            return True
        if i >= len(top):
            return False
        if part != "+" and part != top[i]:
            return False
    return len(pat) == len(top)


class PahoTransport:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "",
        will: tuple[str, bytes | str] | None = None,
    ):
        import paho.mqtt.client as mqtt

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if username:
            self._client.username_pw_set(username, password)
        if will:
            self._client.will_set(will[0], will[1], qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._host = host
        self._port = port

    def start(self, on_message: OnMessage, subscriptions: list[str], on_connect: OnConnect | None = None) -> None:
        def _on_connect(client, _userdata, _flags, reason_code, _props):
            if reason_code.is_failure:
                return
            for topic in subscriptions:
                client.subscribe(topic, qos=1)
            if on_connect:
                on_connect()

        self._client.on_connect = _on_connect
        self._client.on_message = lambda _c, _u, msg: on_message(msg.topic, msg.payload)
        self._client.connect_async(self._host, self._port, keepalive=15)
        self._client.loop_start()

    def publish(self, topic: str, payload: bytes | str, qos: int = 1, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=qos, retain=retain)

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


class LocalBroker:
    """Minimal in-process broker: wildcards and retained messages, delivery on the caller's thread."""

    def __init__(self):
        self._lock = threading.RLock()
        self._clients: list["LocalTransport"] = []
        self._retained: dict[str, bytes] = {}

    def client(self, will: tuple[str, bytes | str] | None = None) -> "LocalTransport":
        return LocalTransport(self, will)

    def _attach(self, client: "LocalTransport") -> None:
        with self._lock:
            self._clients.append(client)
            retained = [(t, p) for t, p in self._retained.items() if client.wants(t)]
        for topic, payload in retained:
            client.deliver(topic, payload)

    def _detach(self, client: "LocalTransport") -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def _publish(self, topic: str, payload: bytes, retain: bool) -> None:
        with self._lock:
            if retain:
                if payload:
                    self._retained[topic] = payload
                else:
                    self._retained.pop(topic, None)
            targets = [c for c in self._clients if c.wants(topic)]
        for client in targets:
            client.deliver(topic, payload)


class LocalTransport:
    def __init__(self, broker: LocalBroker, will: tuple[str, bytes | str] | None):
        self._broker = broker
        self._will = will
        self._subscriptions: list[str] = []
        self._on_message: OnMessage | None = None

    def wants(self, topic: str) -> bool:
        return any(topic_matches(p, topic) for p in self._subscriptions)

    def deliver(self, topic: str, payload: bytes) -> None:
        if self._on_message:
            self._on_message(topic, payload)

    def start(self, on_message: OnMessage, subscriptions: list[str], on_connect: OnConnect | None = None) -> None:
        self._on_message = on_message
        self._subscriptions = list(subscriptions)
        self._broker._attach(self)
        if on_connect:
            on_connect()

    def publish(self, topic: str, payload: bytes | str, qos: int = 1, retain: bool = False) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        self._broker._publish(topic, data, retain)

    def stop(self) -> None:
        self._broker._detach(self)
        self._on_message = None

    def drop(self) -> None:
        """Simulate a lost connection: the broker publishes the last will."""
        self.stop()
        if self._will:
            self.publish(self._will[0], self._will[1], retain=True)
