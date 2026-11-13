from urlkit.query import parse_query


def test_plain_pairs():
    assert parse_query("page=2&sort=asc") == {"page": "2", "sort": "asc"}


def test_flag_without_value():
    assert parse_query("debug&page=1") == {"debug": "", "page": "1"}


def test_duplicates_keep_last():
    assert parse_query("tag=a&tag=b") == {"tag": "b"}
