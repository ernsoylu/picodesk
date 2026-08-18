"""View-model behind the routing matrix (GUI-007 … GUI-011).

Deliberately Qt-free: every rule the matrix enforces lives here as plain
Python so it can be tested directly, and the widget layer stays a thin
rendering of this state.

Rules implemented:
  GUI-008  a consumer is only selectable when data type, width and
           fixed-point scaling match the selected producer exactly
  GUI-009  one writer per inport; removing a model unlinks and unlocks
           every consumer it fed (cascade delete)
  GUI-010  an edge whose endpoints sit in different rate groups is a rate
           transition (ZOH over a bounded seqlock)
  GUI-011  "suggest bindings" proposes exact name+type matches for bulk
           wiring, as a preview that applies (and undoes) atomically
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

HAL_OWNER = "hal"


@dataclass(frozen=True)
class Port:
    owner: str            # model name, or "hal"
    name: str             # port name, or HAL function name
    data_type: str
    width: int = 1
    slope: float = 1.0
    bias: float = 0.0
    rate_group: str | None = None   # None for HAL: inherits its peer
    isr_safe: bool = True

    @property
    def ref(self) -> str:
        return f"{self.owner}.{self.name}"

    @property
    def is_hal(self) -> bool:
        return self.owner == HAL_OWNER

    def type_label(self) -> str:
        label = self.data_type.upper()
        if self.width > 1:
            label += f"[{self.width}]"
        return label

    def compatible_with(self, other: Port) -> tuple[bool, str]:
        """GUI-008: exact match on type, width and scaling — or the reason."""
        if self.data_type != other.data_type:
            return False, f"{self.data_type} ≠ {other.data_type}"
        if self.width != other.width:
            return False, f"width {self.width} ≠ {other.width}"
        if (self.slope, self.bias) != (other.slope, other.bias):
            return False, "fixed-point scaling differs"
        return True, ""


@dataclass(frozen=True)
class Connection:
    producer: str
    consumer: str
    hal_arg: int = 0


@dataclass
class RoutingModel:
    producers: list[Port] = field(default_factory=list)
    consumers: list[Port] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_workspace(cls, descriptor: dict[str, Any],
                       routing: dict[str, Any],
                       hal_manifest: dict[str, dict[str, Any]] | None = None
                       ) -> RoutingModel:
        producers: list[Port] = []
        consumers: list[Port] = []
        for name in sorted(descriptor.get("models", {})):
            model = descriptor["models"][name]
            group = model["rate_group"]
            for port in model["outports"]:
                producers.append(cls._port(name, port, group))
            for port in model["inports"]:
                consumers.append(cls._port(name, port, group))
        for entry in (hal_manifest or {}).values():
            port = Port(owner=HAL_OWNER, name=entry["name"],
                        data_type=entry["data_type"],
                        isr_safe=bool(entry.get("isr_safe", False)))
            (producers if entry["direction"] == "producer"
             else consumers).append(port)
        connections = [
            Connection(c["producer"], c["consumer"], int(c.get("hal_arg", 0)))
            for c in routing.get("connections", [])
        ]
        return cls(producers=producers, consumers=consumers,
                   connections=connections)

    @staticmethod
    def _port(owner: str, port: dict[str, Any], group: str) -> Port:
        return Port(owner=owner, name=port["name"],
                    data_type=port["data_type"], width=int(port.get("width", 1)),
                    slope=float(port.get("slope", 1.0)),
                    bias=float(port.get("bias", 0.0)), rate_group=group)

    def to_routing(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "connections": [
                {"producer": c.producer, "consumer": c.consumer,
                 **({"hal_arg": c.hal_arg} if c.hal_arg else {})}
                for c in self.connections
            ],
        }

    # -- lookup -------------------------------------------------------------

    def producer(self, ref: str) -> Port | None:
        return next((p for p in self.producers if p.ref == ref), None)

    def consumer(self, ref: str) -> Port | None:
        return next((p for p in self.consumers if p.ref == ref), None)

    def writer_of(self, consumer_ref: str) -> str | None:
        """GUI-009: the single producer bound to this inport, if any."""
        return next((c.producer for c in self.connections
                     if c.consumer == consumer_ref), None)

    def is_bound(self, consumer_ref: str) -> bool:
        return self.writer_of(consumer_ref) is not None

    def model_names(self) -> list[str]:
        return sorted({p.owner for p in self.producers + self.consumers
                       if p.owner != HAL_OWNER})

    # -- classification (GUI-010) -------------------------------------------

    def rate_groups_of(self, connection: Connection) -> tuple[str, str]:
        producer = self.producer(connection.producer)
        consumer = self.consumer(connection.consumer)
        model_side = producer if producer and not producer.is_hal else consumer
        fallback = model_side.rate_group if model_side else None
        prod_group = (producer.rate_group if producer else None) or fallback
        cons_group = (consumer.rate_group if consumer else None) or fallback
        return prod_group or "", cons_group or ""

    def mechanism(self, connection: Connection) -> str:
        prod_group, cons_group = self.rate_groups_of(connection)
        return "direct" if prod_group == cons_group else "zoh_seqlock"

    def badge(self, connection: Connection) -> str:
        prod_group, _ = self.rate_groups_of(connection)
        if self.mechanism(connection) == "direct":
            producer = self.producer(connection.producer)
            consumer = self.consumer(connection.consumer)
            if (producer and producer.is_hal) or (consumer and consumer.is_hal):
                return "DIRECT · IN-ISR" if prod_group == "fast_1ms" \
                    else "DIRECT · SAME RATE"
            return "DIRECT · SAME RATE"
        return "RATE TRANSITION · ZOH / SEQLOCK"

    # -- filtering (GUI-008) ------------------------------------------------

    def selectable_consumers(self, producer_ref: str
                             ) -> list[tuple[Port, bool, str]]:
        """Every consumer with (port, selectable, reason-if-not).

        A consumer is unselectable when it is already bound (GUI-009), when
        types do not match exactly (GUI-008), or when binding a non-ISR-safe
        HAL function into the fast loop (GUI-006).
        """
        producer = self.producer(producer_ref)
        rows: list[tuple[Port, bool, str]] = []
        if producer is None:
            return [(c, False, "no producer selected") for c in self.consumers]

        for consumer in self.consumers:
            if self.is_bound(consumer.ref):
                rows.append((consumer, False,
                             f"already bound to {self.writer_of(consumer.ref)}"))
                continue
            if producer.is_hal and consumer.is_hal:
                rows.append((consumer, False, "HAL-to-HAL is not routable"))
                continue
            ok, reason = producer.compatible_with(consumer)
            if not ok:
                rows.append((consumer, False, reason))
                continue
            hal_side = producer if producer.is_hal else (
                consumer if consumer.is_hal else None)
            model_side = consumer if producer.is_hal else producer
            if (hal_side is not None and not hal_side.isr_safe
                    and model_side.rate_group == "fast_1ms"):
                rows.append((consumer, False,
                             f"{hal_side.name} is not ISR-safe (GUI-006)"))
                continue
            rows.append((consumer, True, ""))
        return rows

    # -- mutation -----------------------------------------------------------

    def connect(self, producer_ref: str, consumer_ref: str,
                hal_arg: int = 0) -> Connection:
        allowed = {port.ref for port, ok, _ in
                   self.selectable_consumers(producer_ref) if ok}
        if consumer_ref not in allowed:
            reason = next((r for port, ok, r in
                           self.selectable_consumers(producer_ref)
                           if port.ref == consumer_ref and not ok),
                          "unknown consumer")
            raise ValueError(f"{producer_ref} → {consumer_ref}: {reason}")
        connection = Connection(producer_ref, consumer_ref, hal_arg)
        self.connections.append(connection)
        return connection

    def disconnect(self, consumer_ref: str) -> bool:
        before = len(self.connections)
        self.connections = [c for c in self.connections
                            if c.consumer != consumer_ref]
        return len(self.connections) != before

    def remove_model(self, model: str) -> list[Connection]:
        """GUI-009 cascade delete: drop the model's ports and every edge that
        touched it, so its consumers unlock. Returns the removed edges."""
        removed = [c for c in self.connections
                   if c.producer.split(".", 1)[0] == model
                   or c.consumer.split(".", 1)[0] == model]
        self.connections = [c for c in self.connections if c not in removed]
        self.producers = [p for p in self.producers if p.owner != model]
        self.consumers = [p for p in self.consumers if p.owner != model]
        return removed

    # -- suggestions (GUI-011) ----------------------------------------------

    def suggest_bindings(self) -> list[tuple[Connection, str]]:
        """Exact name + type matches for unbound consumers, with a note.

        Only unambiguous matches are proposed: if two producers could feed
        the same inport, neither is suggested — a wrong bulk-wire is worse
        than no bulk-wire.
        """
        suggestions: list[tuple[Connection, str]] = []
        for consumer in self.consumers:
            if self.is_bound(consumer.ref) or consumer.is_hal:
                continue
            matches = [
                producer for producer in self.producers
                if producer.name == consumer.name
                and producer.owner != consumer.owner
                and producer.compatible_with(consumer)[0]
            ]
            if len(matches) != 1:
                continue
            producer = matches[0]
            connection = Connection(producer.ref, consumer.ref)
            note = ("name + type exact"
                    if self.mechanism(connection) == "direct"
                    else "name + type exact · adds ZOH/seqlock")
            suggestions.append((connection, note))
        return suggestions

    def apply_suggestions(self, connections: Iterable[Connection]
                          ) -> list[Connection]:
        """Apply a previewed set atomically; returns what was applied so the
        caller can hand it straight back to undo_suggestions (GUI-011)."""
        applied: list[Connection] = []
        for connection in connections:
            try:
                applied.append(self.connect(connection.producer,
                                            connection.consumer,
                                            connection.hal_arg))
            except ValueError:
                continue  # became invalid meanwhile; skip rather than corrupt
        return applied

    def undo_suggestions(self, connections: Iterable[Connection]) -> int:
        removed = 0
        for connection in connections:
            if connection in self.connections:
                self.connections.remove(connection)
                removed += 1
        return removed

    # -- summary ------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        model_to_model = sum(
            1 for c in self.connections
            if not (self.producer(c.producer) or Port("", "", "")).is_hal
            and not (self.consumer(c.consumer) or Port("", "", "")).is_hal)
        cross = sum(1 for c in self.connections
                    if self.mechanism(c) == "zoh_seqlock")
        return {
            "total": len(self.connections),
            "model_to_model": model_to_model,
            "hal": len(self.connections) - model_to_model,
            "cross_rate": cross,
            "unbound_consumers": sum(1 for c in self.consumers
                                     if not self.is_bound(c.ref)),
        }
