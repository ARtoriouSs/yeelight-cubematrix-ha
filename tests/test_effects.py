import pytest

from custom_components.cubematrix.effects import EFFECTS


@pytest.mark.parametrize("name", ["fire", "rainbow", "plasma"])
@pytest.mark.parametrize("size", [(5, 5), (15, 5), (5, 15)])
def test_effect_frame_shape(name, size):
    width, height = size
    effect = EFFECTS[name]
    assert effect.name == name
    assert effect.fps > 0
    for index in range(10):
        frame = effect.frame(index, width, height)
        assert len(frame) == width * height
        assert all(len(pixel) == 3 and all(0 <= c <= 255 for c in pixel) for pixel in frame)
        assert frame == effect.frame(index, width, height)


@pytest.mark.parametrize("name", ["fire", "rainbow", "plasma"])
def test_effect_changes_over_time(name):
    effect = EFFECTS[name]
    assert len({effect.frame(i, 5, 5) for i in range(10)}) > 1


def test_fire_hotter_at_bottom():
    fire = EFFECTS["fire"]
    top = bottom = 0
    for index in range(20):
        frame = fire.frame(index, 5, 5)
        top += sum(sum(pixel) for pixel in frame[:5])
        bottom += sum(sum(pixel) for pixel in frame[-5:])
    assert bottom > top
