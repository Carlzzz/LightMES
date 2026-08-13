from lightmes.modules.issue.linkify import issue_linkify


def test_basic():
    assert issue_linkify("see #42 for context") == \
        'see <a href="/issues/42">#42</a> for context'


def test_multiple():
    assert issue_linkify("#1 #2 #3") == \
        '<a href="/issues/1">#1</a> <a href="/issues/2">#2</a> <a href="/issues/3">#3</a>'


def test_does_not_match_hashtag_words():
    """#ABC 不替换（非纯数字）。"""
    assert issue_linkify("#ABC topic") == "#ABC topic"


def test_caps_at_8_digits():
    """#数字 1-8 位才匹配；9 位以上不匹配（避免误匹配大整数）。"""
    assert issue_linkify("#12345678") == '<a href="/issues/12345678">#12345678</a>'
    assert issue_linkify("#123456789") == "#123456789"


def test_empty_input():
    assert issue_linkify(None) == ""
    assert issue_linkify("") == ""


def test_xss_escape():
    """用户输入的 <script> 应被转义。"""
    result = issue_linkify("<script>x</script> #1")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert '<a href="/issues/1">#1</a>' in result


def test_does_not_match_inside_html_entity():
    """escape 后的数字字符实体（&#34; 来自 "）里的 #34 不应被识别为 issue 引用。"""
    result = issue_linkify('see #5 "quoted"')
    assert '<a href="/issues/5">#5</a>' in result
    # " 被 escape 为 &#34;，里面的 #34; 不能变成链接
    assert "/issues/34" not in result
    assert "&#34;" in result  # 实体保持原样


def test_does_not_match_user_typed_entity():
    """用户直接输入的 &#34; 字面也应被 escape 为 &amp;#34; 后不识别。"""
    result = issue_linkify("literal &#34; entity")
    assert "/issues/34" not in result
