import pytest

from custom_components.cubematrix.artformat import (
    ArtError,
    fit_frames,
    parse_art,
    parse_color,
    parse_text,
)

ARROW = """
name: arrow_up
size: 5x5
palette: { "g": "#00ff00" }
frames:
  - |
    . . g . .
    . g g g .
    g . g . g
    . . g . .
    . . g . .
"""
G, O = (0, 255, 0), (0, 0, 0)


def test_parse_text_block():
    [art] = parse_text(ARROW)
    assert (art.name, art.width, art.height, art.fps, art.loop) == ("arrow_up", 5, 5, 5, False)
    assert art.is_static
    assert art.frames[0][:5] == (O, O, G, O, O)
    assert art.spots is None


def test_spaces_between_keys_optional():
    spaced = parse_text(ARROW)[0]
    packed = parse_text(ARROW.replace(". . g . .", "..g..").replace(". g g g .", ".ggg."))[0]
    assert spaced.frames == packed.frames


def test_raw_rgb_rows_and_hex_rows():
    raw = parse_art({"name": "a", "size": "2x1", "frames": [[[[255, 0, 0], [0, 0, 0]]]]})
    hexed = parse_art({"name": "a", "size": "2x1", "frames": [[["#FF0000", "off"]]]})
    assert raw.frames == hexed.frames == (((255, 0, 0), (0, 0, 0)),)


def test_frame_duration_mapping():
    art = parse_art(
        {
            "name": "a",
            "size": "2x1",
            "palette": {"r": "#ff0000"},
            "frames": [{"pixels": "r .", "duration": 0.5}, ". r"],
        }
    )
    assert art.durations == (0.5, None)
    assert not art.is_static
    assert art.frames[1] == (O, (255, 0, 0))


def test_fps_loop_spots():
    art = parse_art(
        {"name": "a", "size": "1x1", "fps": 12, "loop": True, "spots": ["#010203", "off"], "frames": ["."]}
    )
    assert (art.fps, art.loop) == (12, True)
    assert art.spots == ((1, 2, 3), O)


def test_palette_can_redefine_dot():
    art = parse_art({"name": "a", "size": "1x1", "palette": {".": "#0000ff"}, "frames": ["."]})
    assert art.frames[0] == ((0, 0, 255),)


def test_default_name():
    art = parse_art({"size": "1x1", "frames": ["."]}, default_name="from_file")
    assert art.name == "from_file"
    with pytest.raises(ArtError):
        parse_art({"size": "1x1", "frames": ["."]})


def test_list_of_definitions():
    text = (
        "- {name: one, size: 1x1, frames: ['.']}\n"
        "- {name: two, size: 2x1, frames: ['..']}\n"
    )
    arts = parse_text(text)
    assert [(a.name, a.width) for a in arts] == [("one", 1), ("two", 2)]


def test_errors_name_frame_and_row():
    text = ARROW.replace("g . g . g", "g . g .")
    with pytest.raises(ArtError, match="arrow_up: frame 1, row 3: expected 5 pixels, got 4"):
        parse_text(text)


def test_error_in_second_frame_is_numbered():
    frame = "\n".join(["....."] * 5)
    short = "\n".join(["....."] * 4 + ["...."])
    with pytest.raises(ArtError, match="arrow_up: frame 2, row 5"):
        parse_art({"name": "arrow_up", "size": "5x5", "frames": [frame, short]})


def test_wrong_row_count():
    with pytest.raises(ArtError, match="frame 1: expected 2 rows, got 1"):
        parse_art({"name": "a", "size": "1x2", "frames": ["."]})


def test_unknown_palette_key():
    with pytest.raises(ArtError, match="unknown palette key 'x'"):
        parse_text(ARROW.replace(". g g g .", ". x g g ."))


@pytest.mark.parametrize(
    "data",
    [
        {"name": "a", "frames": ["."]},
        {"name": "a", "size": "5by5", "frames": ["....."]},
        {"name": "a", "size": "1x1", "palette": {"r": "#12"}, "frames": ["r"]},
        {"name": "a", "size": "1x1", "frames": [[["#12"]]]},
        {"name": "a", "size": "1x1", "frames": [[[[256, 0, 0]]]]},
        {"name": "a", "size": "1x1"},
        {"name": "a", "size": "1x1", "frames": []},
        {"name": "a", "size": "0x1", "frames": ["."]},
        {"name": "a", "size": "1x1", "palette": {"ab": "#000000"}, "frames": ["."]},
    ],
)
def test_invalid_definitions(data):
    with pytest.raises(ArtError):
        parse_art(data)


def test_parse_text_rejects_bad_yaml_and_shape():
    with pytest.raises(ArtError):
        parse_text("a: [")
    with pytest.raises(ArtError):
        parse_text("just text")


def test_parse_color():
    assert parse_color("#FF8000") == (255, 128, 0)
    assert parse_color("#ff8000") == (255, 128, 0)
    assert parse_color("off") == (0, 0, 0)
    assert parse_color([1, 2, 3]) == (1, 2, 3)
    # Unquoted off in YAML is False
    assert parse_color(False) == (0, 0, 0)
    for bad in ("#12", "red", [1, 2], [1, 2, 256], [1, 2, -1], 5, True, 0):
        with pytest.raises(ArtError):
            parse_color(bad)


def test_unquoted_off_in_palette():
    art = parse_text(
        """
name: dot
size: 2x1
palette:
  ".": off
  "x": off
frames:
  - ". x"
"""
    )[0]
    assert art.frames[0] == ((0, 0, 0), (0, 0, 0))


def _art(name="arrow_up"):
    return parse_text(ARROW)[0]


def test_fit_error_center_tile():
    art = _art()
    for mode in ("error", "center", "tile"):
        assert fit_frames(art, 5, 5, mode) == art.frames

    with pytest.raises(ArtError, match="5x5.*15x5"):
        fit_frames(art, 15, 5, "error")

    [wide] = fit_frames(art, 15, 5, "center")
    for row in range(5):
        line = wide[row * 15 : (row + 1) * 15]
        assert line[5:10] == art.frames[0][row * 5 : (row + 1) * 5]
        assert set(line[:5] + line[10:]) == {O}

    [tiled] = fit_frames(art, 15, 5, "tile")
    for row in range(5):
        line = tiled[row * 15 : (row + 1) * 15]
        expected = art.frames[0][row * 5 : (row + 1) * 5]
        assert line[0:5] == line[5:10] == line[10:15] == expected

    [tall] = fit_frames(art, 5, 15, "center")
    assert tall[25:50] == art.frames[0]
    assert set(tall[:25] + tall[50:]) == {O}


def test_fit_center_odd_gap_puts_extra_on_right():
    art = parse_art({"name": "a", "size": "1x1", "palette": {"r": "#ff0000"}, "frames": ["r"]})
    [frame] = fit_frames(art, 4, 1, "center")
    assert frame == (O, (255, 0, 0), O, O)


def test_fit_rejects_art_larger_than_target():
    with pytest.raises(ArtError):
        fit_frames(_art(), 3, 5, "center")
