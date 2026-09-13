from pathlib import Path
from streamlit.testing.v1 import AppTest
from graphic_culture_atlas.records import Repository

APP = str(Path(__file__).parents[1] / "app.py")


def button(app, label):
    return next(b for b in app.button if b.label == label)


def radio(app, label):
    return next(r for r in app.radio if r.label == label)


def test_empty_workspace_and_import_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path / "data"))
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert any("Start by importing" in i.value for i in app.info)
    radio(app, "Navigate").set_value("Import artwork").run()
    assert not app.exception
    button(app, "Save to local corpus").click().run()
    assert any("Choose an image" in e.value for e in app.error)


def test_offline_full_flow_survives_restart(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("ATLAS_DATA_DIR", str(data))
    app = AppTest.from_file(APP).run()
    button(app, "Load offline example").click().run()
    assert not app.exception
    button(app, "Preview request").click().run()
    button(app, "Run offline fixture").click().run()
    assert not app.exception
    assert len(app.tabs) == 4
    next(t for t in app.text_area if t.label == "Notes, corrections, and evidence").set_value("Human correction.")
    button(app, "Save human review").click().run()
    button(app, "Export local reference package").click().run()
    assert not app.exception
    repo = Repository(data)
    asset = repo.artworks()[0]
    blind = repo.readings(asset["id"])[0]
    assert len(repo.reviews(blind["id"])) == 1
    radio(app, "Reading stage").set_value("With context").run()
    button(app, "Preview request").click().run()
    button(app, "Run offline fixture").click().run()
    assert not app.exception
    assert len(repo.readings(asset["id"])) == 2
    fresh = AppTest.from_file(APP).run()
    assert not fresh.exception
    assert any(s.label == "Saved reading" for s in fresh.selectbox)
    radio(fresh, "Navigate").set_value("Case library").run()
    next(t for t in fresh.text_input if t.label == "Search cases").set_value("Human correction.").run()
    assert any(b.label == "Select artwork" for b in fresh.button)
    assert not fresh.exception


def test_changing_inputs_hides_old_execution_button(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path / "data"))
    app = AppTest.from_file(APP).run()
    button(app, "Load offline example").click().run()
    button(app, "Preview request").click().run()
    assert any(b.label == "Run offline fixture" for b in app.button)
    radio(app, "Reading stage").set_value("With context").run()
    assert not any(b.label == "Run offline fixture" for b in app.button)
    assert not app.exception
