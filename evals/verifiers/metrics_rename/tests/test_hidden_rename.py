import metrics
from metrics.aggregate import average
from metrics.report import summarize


def test_public_api_exposes_average():
    assert metrics.average([2, 4]) == 3.0
    assert average([2, 4]) == 3.0


def test_old_name_is_gone():
    assert not hasattr(metrics, "mean")


def test_report_still_behaves():
    assert summarize([2, 4, 6]) == {"avg": 4.0, "count": 3}
