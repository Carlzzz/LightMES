from dataclasses import dataclass
from lightmes.shared.events import Event, EventBus


@dataclass
class SampleEvent(Event):
    value: int


def test_subscribe_and_publish_calls_handler():
    bus = EventBus()
    received = []
    bus.subscribe(SampleEvent, lambda e: received.append(e.value))
    bus.publish(SampleEvent(value=42))
    assert received == [42]


def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(SampleEvent(value=1))  # 不应抛异常


def test_multiple_handlers_all_called():
    bus = EventBus()
    calls = []
    bus.subscribe(SampleEvent, lambda e: calls.append("a"))
    bus.subscribe(SampleEvent, lambda e: calls.append("b"))
    bus.publish(SampleEvent(value=0))
    assert sorted(calls) == ["a", "b"]
