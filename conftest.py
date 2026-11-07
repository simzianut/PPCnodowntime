import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Point DATA_DIR at an empty dir so tests never read the developer's local data/."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data_dir")))
