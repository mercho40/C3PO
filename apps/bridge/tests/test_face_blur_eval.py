"""The pure halves of the face-blur eval: what the distance limit and the labels mean.

The measurement itself needs labelled photos (and the Orin, for latency). These
decide what its numbers say.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from face_blur_eval import max_face_distance_m, parse_labels, score  # noqa: E402


def test_the_videohub_feed_loses_frontal_faces_past_about_three_metres():
    # 1920 px at 69.4 deg -> f ~ 1386 px; detected at x1/3, so 20 px there is
    # 60 px of the frame, which a 14 cm face is at ~3.2 m.
    assert max_face_distance_m(1920, 69.4) == pytest.approx(3.24, abs=0.01)


def test_a_frame_narrower_than_the_detection_copy_is_not_downscaled():
    # 640 px at 55 deg is fx ~ 615 px, the vision container's own colour stream.
    assert max_face_distance_m(640, 55.0) == pytest.approx(0.14 * 614.7 / 20, abs=0.01)
    assert max_face_distance_m(640, 55.0, detect_w=320) == pytest.approx(
        max_face_distance_m(640, 55.0) / 2
    )


def test_labels_group_faces_per_image_and_keep_faceless_images():
    labels = parse_labels(
        [
            "file,x0,y0,x1,y1",
            "a.jpg,10,20,40,60",
            " a.jpg , 100 ,20,130,60",
            "",
            "b.jpg,,,,",
        ]
    )
    assert labels == {"a.jpg": [(10, 20, 40, 60), (100, 20, 130, 60)], "b.jpg": []}


@pytest.mark.parametrize(
    ("lines", "where"),
    [
        (["file,x,y,w,h"], "line 1"),
        (["file,x0,y0,x1,y1", "a.jpg,10,20,40"], "line 2"),
        (["file,x0,y0,x1,y1", "a.jpg,1,1,2,2", "a.jpg,10.5,20,40,60"], "line 3"),
        (["file,x0,y0,x1,y1", "a.jpg,40,20,10,60"], "line 2"),  # corners swapped
    ],
)
def test_a_bad_label_file_names_the_line(lines, where):
    with pytest.raises(ValueError, match=where):
        parse_labels(lines)


def test_a_face_needs_half_its_box_pixelated_and_a_stray_region_is_a_false_positive():
    faces = [(0, 0, 100, 100), (200, 0, 300, 100)]
    regions = [(0, 0, 100, 60), (200, 0, 300, 40), (500, 500, 520, 520)]
    missed, hits = score(faces, regions)
    assert missed == [(200, 0, 300, 100)]  # 40 % covered: still recognisable
    assert hits == 2
