from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Event:
    """所有领域事件的基类。"""


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: Callable[[Event], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._handlers[type(event)]:
            handler(event)


event_bus = EventBus()
