"""Parsing tegrastats and judging the compute budget, against known lines.

A wrong parser here is hardest to notice in exactly the condition it is used
in — a robot under sustained load — and the number it produces becomes the
answer to "does this stack fit", which decides whether the failure mode takes
`stop_everything` with it.
"""

from __future__ import annotations

from bringup.measure import (
    CORE_HI_FAIL_PCT,
    RAM_FAIL_MB,
    Sample,
    judge,
    longest_run,
    parse_tegrastats,
    rate_verdict,
    summarise,
)

LINE = (
    "RAM 3456/15388MB (lfb 20x4MB) SWAP 0/7694MB (cached 0MB) "
    "CPU [12%@1420,8%@1420,off,off,5%@1420,3%@1420,2%@1420,1%@1420] "
    "EMC_FREQ 24%@2133 GR3D_FREQ 0%@[306] "
    "cpu@52.5C soc2@50C soc0@50C gpu@51C tj@53.5C soc1@50C VDD_IN 5000mW/5000mW"
)


def status_of(verdicts, name):
    return next(v.status for v in verdicts if v.name == name)


# --- parsing ----------------------------------------------------------------


def test_ram_is_read_as_used_and_total():
    s = parse_tegrastats(LINE)
    assert (s.ram_mb, s.ram_total_mb) == (3456, 15388)


def test_lfb_is_the_pool_not_the_block_size():
    """20x4MB is an 80 MB pool with a 4 MB largest block.

    Judging the 200 MB floor on the block SIZE would fail an idle robot — the
    largest block was 4 MB at the 2026-08-18 baseline.
    """
    s = parse_tegrastats(LINE)
    assert s.lfb_pool_mb == 80
    assert s.lfb_block_mb == 4


def test_offline_cores_are_none_not_zero():
    """A powered-down core is not an idle core.

    Averaging it in as zero makes a saturated pair look half-loaded.
    """
    s = parse_tegrastats(LINE)
    assert s.cores[2] is None and s.cores[3] is None
    assert s.cores[0] == 12


def test_the_bridge_cores_are_indexed_from_zero():
    """The shell version's awk used `i >= 6` over a 1-indexed split to mean
    "core 5", which is the kind of off-by-one nobody re-derives correctly."""
    s = parse_tegrastats(LINE)
    assert s.core_max([5, 6, 7]) == 3  # 3%, 2%, 1%
    assert s.core_max([6, 7]) == 2


def test_offline_cores_do_not_drag_the_max_to_none():
    s = parse_tegrastats(LINE)
    assert s.core_max([2, 3, 0]) == 12  # two offline, one at 12%


def test_all_offline_is_unknown_rather_than_zero():
    s = parse_tegrastats(LINE)
    assert s.core_max([2, 3]) is None


def test_temperature_and_emc_are_read():
    s = parse_tegrastats(LINE)
    assert s.tj_c == 53.5
    assert s.emc_pct == 24


def test_clocks_average_only_the_online_cores():
    s = parse_tegrastats(LINE)
    assert s.mean_clock_mhz() == 1420


def test_a_line_that_is_not_tegrastats_yields_nothing_rather_than_zeros():
    """Zeros would read as a perfectly idle machine."""
    s = parse_tegrastats("some unrelated log line")
    assert s.ram_mb is None and s.tj_c is None and s.cores == {}


# --- sustained --------------------------------------------------------------


def test_sustained_means_consecutive_not_total():
    """Sixty scattered seconds over an hour is a different machine from sixty
    seconds straight, and only the second is the failure being looked for."""
    assert longest_run([True, False, True, False, True]) == 1
    assert longest_run([False, True, True, True, False]) == 3
    assert longest_run([]) == 0


# --- judging ----------------------------------------------------------------


def sample(**kw):
    return Sample(**kw)


def test_no_samples_is_unknown_not_a_pass():
    verdicts = judge([])
    assert verdicts[0].status == "UNKNOWN"


def test_memory_over_the_threshold_fails():
    verdicts = judge([sample(ram_mb=RAM_FAIL_MB + 1)], sustain_n=1)
    assert status_of(verdicts, "memory") == "FAIL"


def test_memory_under_the_threshold_passes():
    verdicts = judge([sample(ram_mb=4000)], sustain_n=1)
    assert status_of(verdicts, "memory") == "PASS"


def test_any_swap_at_all_is_a_failure():
    """Swapping on a unified-memory part means the budget is already blown."""
    verdicts = judge([sample(swap_mb=1)], sustain_n=1)
    assert status_of(verdicts, "swap") == "FAIL"


def test_a_brief_core_spike_is_not_a_sustained_breach():
    hot = sample(cores={5: CORE_HI_FAIL_PCT + 10, 6: 1, 7: 1})
    cool = sample(cores={5: 1, 6: 1, 7: 1})
    verdicts = judge([hot, cool, hot, cool], sustain_n=3)
    assert status_of(verdicts, "cores 5-7") == "PASS"


def test_a_sustained_core_breach_fails():
    hot = sample(cores={5: CORE_HI_FAIL_PCT + 10, 6: 1, 7: 1})
    verdicts = judge([hot, hot, hot], sustain_n=3)
    assert status_of(verdicts, "cores 5-7") == "FAIL"


def test_core_five_is_reported_apart_from_six_and_seven():
    """perception_up pins the vision container to core 5, so a core-5 breach
    may be that pin rather than a budget breach."""
    hot5 = sample(cores={5: 99, 6: 1, 7: 1})
    verdicts = judge([hot5, hot5, hot5], sustain_n=3)
    assert status_of(verdicts, "cores 5-7") == "FAIL"
    assert status_of(verdicts, "cores 6-7") == "PASS"


def test_missing_measurements_are_unknown_rather_than_pass():
    """A run where tegrastats produced nothing must not read as a run where the
    budget was fine."""
    verdicts = judge([sample(ram_mb=4000)], sustain_n=1)
    assert status_of(verdicts, "thermal") == "UNKNOWN"
    assert status_of(verdicts, "cores 5-7") == "UNKNOWN"


# --- rates ------------------------------------------------------------------


def test_a_rate_below_its_floor_fails():
    assert rate_verdict("/odom", 4.0).status == "FAIL"


def test_a_rate_at_its_floor_passes():
    assert rate_verdict("/odom", 9.0).status == "PASS"


def test_an_unmeasured_rate_is_unknown():
    assert rate_verdict("/odom", None).status == "UNKNOWN"


# --- the summary ------------------------------------------------------------


def test_unknowns_alone_are_not_a_pass():
    fails, warns, unknowns, closing = summarise(judge([]))
    assert (fails, warns, unknowns) == (0, 0, 1)
    assert "not a pass" in closing


def test_a_failure_says_the_stack_does_not_fit():
    verdicts = judge([sample(ram_mb=RAM_FAIL_MB + 1, tj_c=20.0, cores={5: 1, 6: 1, 7: 1},
                             clocks_mhz=[1500], emc_pct=1, lfb_pool_mb=900, swap_mb=0)],
                     sustain_n=1)
    fails, _, _, closing = summarise(verdicts)
    assert fails == 1
    assert "does not fit" in closing


def test_a_clean_run_says_so():
    good = sample(ram_mb=4000, tj_c=50.0, cores={5: 1, 6: 1, 7: 1},
                  clocks_mhz=[1500], emc_pct=10, lfb_pool_mb=900, swap_mb=0)
    fails, warns, unknowns, closing = summarise(judge([good], sustain_n=1))
    assert (fails, warns, unknowns) == (0, 0, 0)
    assert "inside budget" in closing
