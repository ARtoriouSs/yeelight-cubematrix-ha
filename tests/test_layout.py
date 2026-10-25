import pytest

from custom_components.cubematrix.layout import (
    Layout,
    encode_leds,
    module_led_order,
    parse_modules,
)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


def frame_with(width, height, pixels, fill=BLACK):
    cells = [fill] * (width * height)
    for (x, y), colour in pixels.items():
        cells[y * width + x] = colour
    return tuple(cells)


def test_led0_is_bottom_right():
    order = module_led_order("row", False)
    assert order[0] == (4, 4) and order[1] == (3, 4) and order[5] == (4, 3)


def test_column_and_serpentine_orders():
    assert module_led_order("column", False)[1] == (4, 3)
    assert module_led_order("row", True)[5] == (0, 3)


def test_single_matrix_bytes():
    raw = Layout().led_bytes(frame_with(5, 5, {(4, 4): WHITE}))
    assert len(raw) == 75
    assert raw[0:3] == bytes([255, 255, 255])
    assert raw[3:] == bytes(72)


def test_two_matrices_horizontal():
    layout = Layout(("matrix", "matrix"))
    assert (layout.width, layout.height) == (10, 5)
    raw = layout.led_bytes(frame_with(10, 5, {(4, 4): WHITE}))
    assert raw[0:3] == bytes([255, 255, 255])
    raw = layout.led_bytes(frame_with(10, 5, {(9, 4): WHITE}))
    assert raw[25 * 3:25 * 3 + 3] == bytes([255, 255, 255])
    assert raw[0:3] == bytes(3)


def test_vertical_chain_first_module_is_bottom():
    layout = Layout(("matrix", "matrix"), "vertical")
    assert (layout.width, layout.height) == (5, 10)
    raw = layout.led_bytes(frame_with(5, 10, {(4, 9): WHITE}))
    assert raw[0:3] == bytes([255, 255, 255])


def test_spot_slots_and_average():
    layout = Layout(("matrix", "spot"))
    assert (layout.width, layout.height, layout.spot_count) == (5, 5, 1)
    frame = frame_with(5, 5, {})
    raw = layout.led_bytes(frame, spots=[(10, 20, 30)])
    assert len(raw) == (25 + 5) * 3
    assert raw[-15:] == bytes([10, 20, 30]) * 5
    raw = layout.led_bytes(frame_with(5, 5, {}, fill=(100, 0, 0)))
    assert raw[-15:] == bytes([100, 0, 0]) * 5


def test_short_spots_padded_with_average_and_extras_ignored():
    layout = Layout(("spot", "matrix", "spot"))
    frame = frame_with(5, 5, {}, fill=(40, 0, 0))
    raw = layout.led_bytes(frame, spots=[(1, 2, 3)])
    assert len(raw) == (5 + 25 + 5) * 3
    assert raw[:15] == bytes([1, 2, 3]) * 5
    # The missing second spot gets the average canvas colour
    assert raw[-15:] == bytes([40, 0, 0]) * 5
    raw = layout.led_bytes(frame, spots=[(1, 2, 3), (4, 5, 6), (7, 8, 9)])
    assert len(raw) == (5 + 25 + 5) * 3
    assert raw[-15:] == bytes([4, 5, 6]) * 5


def test_brightness_scales():
    raw = Layout().led_bytes(frame_with(5, 5, {}, fill=(255, 0, 0)), brightness=128)
    assert raw[0:3] == bytes([128, 0, 0])


def test_parse_modules():
    assert parse_modules("matrix, matrix,spot") == ("matrix", "matrix", "spot")
    with pytest.raises(ValueError):
        parse_modules("spot")
    with pytest.raises(ValueError):
        parse_modules("lamp")


def test_encode_leds():
    assert encode_leds(bytes([255, 0, 0])) == "/wAA"
