from urlkit import parse_query


def test_plus_is_decoded_as_space():
    assert parse_query("q=hello+world") == {"q": "hello world"}


def test_percent_escapes_are_decoded():
    assert parse_query("path=a%2Fb&q=caf%C3%A9") == {"path": "a/b", "q": "café"}


def test_keys_are_decoded():
    assert parse_query("first%20name=Ada") == {"first name": "Ada"}


def test_plain_pairs_still_work():
    assert parse_query("page=2&sort=asc") == {"page": "2", "sort": "asc"}


def test_flag_without_value_still_maps_to_empty_string():
    assert parse_query("debug&page=1") == {"debug": "", "page": "1"}
