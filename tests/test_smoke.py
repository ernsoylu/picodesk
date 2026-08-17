"""Smoke test: the host package imports and exposes a version."""


def test_import() -> None:
    import picodesk

    assert picodesk.__version__
