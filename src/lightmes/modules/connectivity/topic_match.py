"""MQTT topic wildcard matching (MQTT spec: + = single level, # = multi level suffix)."""


def matches_topic(pattern: str, topic: str) -> bool:
    """Check if a topic matches an MQTT subscription pattern.

    MQTT wildcards:
        + : exactly one level (e.g. "machine/+/count" matches "machine/L1/count")
        # : zero or more levels at end (e.g. "machine/#" matches "machine/L1/a/b")
    """
    if pattern == topic:
        return True

    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")

    # # multi-level wildcard must be the last segment per MQTT spec
    if pattern_parts[-1] == "#":
        # Drop the trailing "#" and compare segment-by-segment with leading topic parts.
        head = pattern_parts[:-1]
        # Topic must have at least len(head) levels (then zero or more extra).
        if len(topic_parts) < len(head):
            return False
        for p, t in zip(head, topic_parts[: len(head)]):
            if p != "+" and p != t:
                return False
        return True

    # + single-level per segment (no # present)
    if len(pattern_parts) != len(topic_parts):
        return False
    for p, t in zip(pattern_parts, topic_parts):
        if p != "+" and p != t:
            return False
    return True
