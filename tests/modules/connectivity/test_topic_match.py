from lightmes.modules.connectivity.topic_match import matches_topic


def test_exact_match():
    assert matches_topic("machine/line1/count", "machine/line1/count") is True


def test_no_match():
    assert matches_topic("machine/line1/count", "machine/line1/alarm") is False


def test_plus_single_level_wildcard():
    assert matches_topic("machine/+/count", "machine/line1/count") is True
    assert matches_topic("machine/+/count", "machine/line2/count") is True
    assert matches_topic("machine/+/count", "machine/line1/alarm") is False
    # + 不匹配多层
    assert matches_topic("machine/+/count", "machine/line1/sub/count") is False


def test_hash_multi_level_wildcard():
    assert matches_topic("machine/line1/#", "machine/line1/count") is True
    assert matches_topic("machine/line1/#", "machine/line1/sub/deep/path") is True
    assert matches_topic("machine/line1/#", "machine/line2/count") is False
    # 顶层 # 匹配所有
    assert matches_topic("#", "anything/anywhere") is True
    assert matches_topic("#", "top") is True


def test_plus_and_hash_combined():
    assert matches_topic("+/line1/#", "a/line1/b/c") is True
    assert matches_topic("+/line1/#", "a/line2/b/c") is False
