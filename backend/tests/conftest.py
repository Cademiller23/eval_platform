import pytest


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_DATA_DIR", str(tmp_path / "data"))
    from evalplatform import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
