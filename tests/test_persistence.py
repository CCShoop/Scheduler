from libs.persistence import Persistence


def test_write_then_read_round_trips(tmp_path):
    persist = Persistence(str(tmp_path / "data.json"))
    persist.write({"events": [{"name": "test"}]})
    assert persist.read() == {"events": [{"name": "test"}]}


def test_read_missing_file_returns_none(tmp_path):
    assert Persistence(str(tmp_path / "missing.json")).read() is None


def test_paused_skips_read_and_write(tmp_path):
    path = tmp_path / "data.json"
    persist = Persistence(str(path))
    persist.write({"saved": True})
    persist.pause()
    persist.write({"saved": False})
    assert persist.read() is None
    persist.resume()
    assert persist.read() == {"saved": True}


def test_write_overwrites_previous_contents(tmp_path):
    persist = Persistence(str(tmp_path / "data.json"))
    persist.write({"events": [1, 2, 3]})
    persist.write({"events": []})
    assert persist.read() == {"events": []}
