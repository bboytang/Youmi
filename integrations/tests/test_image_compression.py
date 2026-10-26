import io

from PIL import Image

from integrations.astrbot_plugin_youmi.media import compress_image


def test_large_static_image_is_smaller_and_limited_without_changing_source(tmp_path):
    path = tmp_path / 'photo.png'
    Image.effect_noise((2200, 1600), 80).convert('RGB').save(path)
    original = path.read_bytes()
    compressed = compress_image(path)
    assert compressed and len(compressed) < len(original)
    with Image.open(io.BytesIO(compressed)) as result:
        assert max(result.size) == 1280
    assert path.read_bytes() == original


def test_animation_is_left_intact(tmp_path):
    path = tmp_path / 'animated.gif'
    frames = [Image.new('RGB', (30, 30), color) for color in ('red', 'blue')]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert compress_image(path) is None
    with Image.open(path) as result:
        assert result.n_frames == 2


def test_transparent_image_keeps_alpha(tmp_path):
    path = tmp_path / 'transparent.png'
    Image.effect_noise((1600, 1200), 60).convert('RGBA').save(path, compress_level=0)
    compressed = compress_image(path)
    assert compressed
    with Image.open(io.BytesIO(compressed)) as result:
        assert 'A' in result.getbands() and max(result.size) == 1280
