import pytest
from pydantic import ValidationError

from cwjh.protocol import Move, Pour
from cwjh.transport import topic_matches


@pytest.mark.parametrize(("degrees", "expected"), [(-5, 0), (0, 0), (12.5, 12.5), (30, 30), (45, 30)])
def test_move_degrees_are_cut_to_limit(degrees, expected):
    assert Move(bay=1, degrees=degrees).degrees == expected


@pytest.mark.parametrize("bay", [0, 13])
def test_bay_out_of_range_is_rejected(bay):
    with pytest.raises(ValidationError):
        Pour(bay=bay, ml=10)


@pytest.mark.parametrize("ml", [0, -1, 401])
def test_pour_volume_limits(ml):
    with pytest.raises(ValidationError):
        Pour(bay=1, ml=ml)


def test_pour_density_limits():
    with pytest.raises(ValidationError):
        Pour(bay=1, ml=10, g_per_ml=3.0)


def test_command_ids_are_unique():
    assert Pour(bay=1, ml=10).id != Pour(bay=1, ml=10).id


@pytest.mark.parametrize(
    ("pattern", "topic", "match"),
    [
        ("cwjh/cmd/#", "cwjh/cmd/glass/init", True),
        ("cwjh/cmd/#", "cwjh/hub/ack", False),
        ("cwjh/+/ack", "cwjh/hub/ack", True),
        ("cwjh/hub/ack", "cwjh/hub/ack/x", False),
        ("cwjh/hub/ack/x", "cwjh/hub/ack", False),
    ],
)
def test_topic_matching(pattern, topic, match):
    assert topic_matches(pattern, topic) is match
