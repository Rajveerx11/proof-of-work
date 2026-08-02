from limits import clamp


def test_clamp_boundaries():
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
    assert clamp(4, 0, 10) == 4
