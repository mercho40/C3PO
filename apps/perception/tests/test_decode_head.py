"""Output decoding for both head layouts, and instance masks, on synthetic tensors.

NEEDS NUMPY, WHICH THIS HARNESS DELIBERATELY DOES NOT INSTALL (see
pyproject.toml), so the whole file skips under a plain `uv run pytest`. Run it
with:

    uv run --with numpy python -m pytest tests/test_decode_head.py

(`python -m`, not the bare `pytest` script: that one is pinned to the project
venv and never sees the `--with` overlay, so the file would still skip.)

The shapes are the ones measured from real exports (2026-11-02, ultralytics
8.4.198): YOLO11n raw (1, 84, 8400); YOLOE-26s-seg NMS-free (1, 300, 38) +
protos (1, 32, 160, 160); YOLOE-26s-seg raw (1, 4 + 1223 + 32, 8400).
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from c3po_vision.detector import anchor_count, decode_head, instance_mask

NET = (640, 640)
# The D435i stream is 640x480 into a 640x640 network: scale 1, 80 px pad top.
GEOM = {"net_hw": NET, "scale": 1.0, "pad_x": 0, "pad_y": 80, "width": 640, "height": 480}


def raw_head(nc: int, nm: int = 0):
    out = np.zeros((1, 4 + nc + nm, anchor_count(*NET)), np.float32)
    # one object: centre (320, 280) network px = (320, 200) image px, 100x60
    out[0, :4, 7] = (320, 280, 100, 60)
    out[0, 4 + 2, 7] = 0.9
    out[0, :4, 8] = (322, 281, 100, 60)       # its duplicate, same class
    out[0, 4 + 2, 8] = 0.8
    if nm:
        out[0, 4 + nc:, :] = 5.0              # huge coefficients must not read as classes
    return out


def test_the_anchor_count_identifies_a_640_head():
    assert anchor_count(640, 640) == 8400


def test_a_raw_head_decodes_to_one_box_in_image_pixels():
    (det,) = decode_head(np, raw_head(80), **GEOM)
    cls, conf, box, coeffs = det
    assert (cls, round(conf, 2), coeffs) == (2, 0.9, None)
    assert box == pytest.approx((270, 170, 370, 230))


def test_the_transposed_raw_layout_decodes_the_same():
    a = decode_head(np, raw_head(80), **GEOM)
    b = decode_head(np, raw_head(80).transpose(0, 2, 1).copy(), **GEOM)
    assert [d[:3] for d in a] == [d[:3] for d in b]


def test_mask_coefficients_are_never_mistaken_for_classes():
    (det,) = decode_head(np, raw_head(1223, nm=32), num_masks=32, **GEOM)
    assert det[0] == 2 and det[3].shape == (32,)


def test_an_nms_free_head_needs_no_nms_and_drops_low_scores():
    out = np.zeros((1, 300, 38), np.float32)
    out[0, 0, :6] = (270, 250, 370, 310, 0.9, 1222)
    out[0, 1, :6] = (10, 90, 50, 130, 0.2, 5)       # below threshold
    dets = decode_head(np, out, num_masks=32, conf_threshold=0.35, **GEOM)
    assert len(dets) == 1
    cls, conf, box, coeffs = dets[0]
    assert cls == 1222 and conf == pytest.approx(0.9)
    assert box == pytest.approx((270, 170, 370, 230))
    assert coeffs.shape == (32,)


def test_an_instance_mask_follows_the_prototype_under_the_box():
    protos = np.full((1, 32, 160, 160), -1.0, np.float32)
    # Prototype 0 is "on" over network rows 260..300 (image rows 180..220).
    protos[0, 0, 65:75, :] = 1.0
    coeffs = np.zeros(32, np.float32)
    coeffs[0] = 1.0
    mask = instance_mask(np, coeffs, protos, (270, 170, 370, 230), net_hw=NET,
                         scale=1.0, pad_x=0, pad_y=80)
    assert mask.shape == (60, 100)
    assert not mask[:10].any() and mask[10:50].all() and not mask[50:].any()
