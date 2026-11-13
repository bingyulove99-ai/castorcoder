import pytest

from metrics import mean, summarize


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_rejects_empty():
    with pytest.raises(ValueError):
        mean([])


def test_summarize():
    assert summarize([2, 4, 6]) == {"avg": 4.0, "count": 3}
