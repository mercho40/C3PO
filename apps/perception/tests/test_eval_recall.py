"""The pure half of tools/eval_recall.py: banding, label parsing, matching, the summary.

These numbers decide whether YOLOE replaces YOLO11n, so the arithmetic is
pinned here; the model and file half runs on the robot.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import eval_recall as ev


def test_bands_follow_the_placement_distances():
    assert [ev.band_for(z) for z in (0.4, 0.99, 1.0, 2.0, 2.3, 4.0, None)] == [
        "0.5m", "0.5m", "1.5m", "1.5m", "3m", "3m", "unknown"]
    assert ev.band_from_name("/x/d1.5_003") == "1.5m"
    assert ev.band_from_name("d3_000") == "3m"
    assert ev.band_from_name("f_000") is None


def test_yolo_labels_become_pixel_boxes():
    (label, box), = ev.read_yolo_labels("1 0.5 0.5 0.25 0.5\n", ["mug", "wallet"], 640, 480)
    assert label == "wallet" and box == (240.0, 120.0, 400.0, 360.0)


def test_an_alias_counts_and_a_prediction_is_used_once():
    gts = [("television set", (0, 0, 100, 100)), ("television set", (0, 0, 100, 100))]
    preds = [("tv", 0.9, (0, 0, 100, 100))]
    assert ev.match(gts, preds, ev.DEFAULT_ALIASES) == [True, False]


def test_a_wrong_label_or_a_poor_overlap_is_a_miss():
    gts = [("wallet", (0, 0, 100, 100))]
    assert ev.match(gts, [("book", 0.9, (0, 0, 100, 100))], {}) == [False]
    assert ev.match(gts, [("wallet", 0.9, (60, 60, 160, 160))], {}) == [False]


def test_the_headline_is_the_mean_of_per_class_recall_per_band():
    records = [("mug", "0.5m", True), ("mug", "0.5m", True),
               ("wallet", "0.5m", False), ("wallet", "0.5m", True),
               ("keys", "3m", False)]
    s = ev.summarize(records)
    assert s["mean_class_recall"] == {"0.5m": 0.75, "3m": 0.0}
    assert s["per_class"]["wallet"]["0.5m"] == [1, 2]
    table = ev.format_table(s)
    assert "| wallet | 1/2 | - |" in table and "**75%**" in table
