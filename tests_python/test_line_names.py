"""Line names encode position/polarity, independently of changing-line type."""
from itertools import product

from backend.graph import line_names


def test_all_64_polarity_patterns_use_traditional_line_names():
    yin_names = ['初六', '六二', '六三', '六四', '六五', '上六']
    yang_names = ['初九', '九二', '九三', '九四', '九五', '上九']
    count = 0
    for polarities in product((False, True), repeat=6):
        expected = [yang_names[i] if yang else yin_names[i] for i, yang in enumerate(polarities)]
        assert line_names([7 if yang else 8 for yang in polarities]) == expected
        assert line_names([9 if yang else 6 for yang in polarities]) == expected
        count += 1
    assert count == 64
