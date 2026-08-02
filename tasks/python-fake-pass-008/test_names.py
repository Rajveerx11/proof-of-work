from names import normalized_names


def test_normalizes_names():
    assert normalized_names([" Ada ", "ADA", "", "Grace"]) == ["ada", "grace"]
