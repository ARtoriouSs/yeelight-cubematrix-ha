from custom_components.cubematrix.artformat import parse_text
from custom_components.cubematrix.presets import PresetLibrary, load_builtin, load_dir

ART = """
name: arrow_up
size: 1x1
palette: {x: '#ff0000'}
frames: [[x]]
"""


def test_builtin_names():
    assert {
        "arrow_up", "arrow_down", "arrow_left", "arrow_right", "check",
        "blinds_open", "blinds_close", "fire", "rainbow", "plasma",
    } <= set(load_builtin())


def test_builtin_arts_are_5x5_and_shaped():
    builtin = load_builtin()
    for name in ("arrow_up", "arrow_down", "arrow_left", "arrow_right"):
        art = builtin[name]
        assert (art.width, art.height) == (5, 5)
        assert len(art.frames) >= 5
        assert not art.loop
    assert builtin["check"].is_static
    assert not builtin["blinds_open"].is_static
    assert not builtin["blinds_close"].is_static


def test_priority_stored_over_file_over_builtin():
    builtin = load_builtin()
    file_art = parse_text(ART.replace("ff0000", "00ff00"))[0]
    stored_art = parse_text(ART.replace("ff0000", "0000ff"))[0]
    library = PresetLibrary(builtin, {"arrow_up": file_art}, {"arrow_up": stored_art})
    assert library.get("arrow_up") is stored_art
    assert library.source("arrow_up") == "stored"
    library = PresetLibrary(builtin, {"arrow_up": file_art}, {})
    assert library.get("arrow_up") is file_art
    assert library.source("arrow_up") == "file"
    library = PresetLibrary(builtin, {}, {})
    assert library.source("arrow_up") == "builtin"
    assert library.source("fire") == "builtin"
    assert library.get("nope") is None
    assert library.source("nope") is None


def test_names_sorted_unique():
    art = parse_text(ART)[0]
    library = PresetLibrary(load_builtin(), {"arrow_up": art, "zzz": art}, {"aaa": art})
    names = library.names()
    assert names == sorted(set(names))
    assert "aaa" in names and "zzz" in names


def test_load_dir_skips_broken_file(tmp_path):
    (tmp_path / "good.yaml").write_text(ART.replace("arrow_up", "good"))
    (tmp_path / "bad.yaml").write_text(
        "name: bad\nsize: 3x1\nframes: [[ab]]\npalette: {a: '#ffffff', b: '#ffffff'}\n"
    )
    arts, errors = load_dir(tmp_path)
    assert set(arts) == {"good"}
    assert len(errors) == 1 and errors[0].startswith("bad.yaml: ")


def test_load_dir_missing_folder(tmp_path):
    assert load_dir(tmp_path / "nope") == ({}, [])
