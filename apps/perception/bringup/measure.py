"""The compute-budget harness: parse tegrastats, judge against fixed thresholds.

`docs/OPERATIONS.md` §9 records that nobody has measured whether this stack
fits, and that the failure mode takes the bridge with it — i.e. takes
`stop_everything` with it. "It felt fine" is not a measurement, and neither is a
number read after the fact against a threshold chosen after the fact. So the
thresholds are stated before the run and the verdict is mechanical.

This module is the parsing and the judging. It changes nothing, starts nothing
and claims no sensor — and being pure is what lets the judgements be checked
against known tegrastats lines instead of against a robot under load, which is
the one condition where a wrong parser is hardest to notice.

THE 16 GB ON AN ORIN NX IS UNIFIED CPU+GPU. The CUDA context, the TensorRT
engine and the RealSense buffers all draw from the same pool FAST-LIO's ikd-Tree
grows into, so tegrastats' `RAM` is the whole budget, not the CPU's share.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# --- thresholds, fixed in advance -------------------------------------------
#
# Straight out of apps/perception/README.md Stage 7. Do not edit them to make a
# run pass; edit them only with the reason written down, because their whole
# value is being fixed before the measurement.

RAM_TOTAL_MB = 15388
RAM_FAIL_MB = 11776  # 11.5 GB of 15.0, unified CPU+GPU
LFB_FAIL_MB = 200  # free-block POOL floor, not the block size — see below
LOAD_FAIL = 8.0
CORE_HI_FAIL_PCT = 50
TJ_FAIL_C = 90.0
CLK_FAIL_MHZ = 1400
EMC_WARN_PCT = 60
EMC_FAIL_PCT = 80
SUSTAIN_S = 60

RATE_MIN = {
    "/livox/lidar": 9.0,
    "/odom": 9.0,
    "/c3po/objects": 10.0,
    "/c3po/world_summary": 3.5,
}

#: The cores the bridge and the OS live on. The bridge owns stop_everything, so
#: these staying free is a safety property, not a performance one.
BRIDGE_CORES = (5, 6, 7)
#: perception_up pins the VISION container to core 5, so a core-5 breach may be
#: that pin rather than a budget breach. Reported separately for that reason.
UNPINNED_BRIDGE_CORES = (6, 7)


class Sample:
    """One tegrastats line, parsed. Missing fields are None, never zero."""

    def __init__(
        self,
        ram_mb: Optional[int] = None,
        ram_total_mb: Optional[int] = None,
        lfb_pool_mb: Optional[int] = None,
        lfb_block_mb: Optional[int] = None,
        swap_mb: Optional[int] = None,
        cores: Optional[Dict[int, Optional[int]]] = None,
        clocks_mhz: Optional[List[int]] = None,
        tj_c: Optional[float] = None,
        emc_pct: Optional[int] = None,
    ) -> None:
        self.ram_mb = ram_mb
        self.ram_total_mb = ram_total_mb
        self.lfb_pool_mb = lfb_pool_mb
        self.lfb_block_mb = lfb_block_mb
        self.swap_mb = swap_mb
        self.cores = cores or {}
        self.clocks_mhz = clocks_mhz or []
        self.tj_c = tj_c
        self.emc_pct = emc_pct

    def core_max(self, which: Sequence[int]) -> Optional[int]:
        """Highest busy percentage among `which`. None if none reported.

        An OFFLINE core is None, not 0: a core that is powered down is not a
        core that is idle, and averaging it in as zero would make a saturated
        pair look half-loaded.
        """
        values = [self.cores.get(c) for c in which]
        present = [v for v in values if v is not None]
        return max(present) if present else None

    def mean_clock_mhz(self) -> Optional[int]:
        return int(sum(self.clocks_mhz) / len(self.clocks_mhz)) if self.clocks_mhz else None


_RAM = re.compile(r"\bRAM (\d+)/(\d+)MB")
_LFB = re.compile(r"\blfb (\d+)x(\d+)MB")
_SWAP = re.compile(r"\bSWAP (\d+)/(\d+)MB")
_CPU = re.compile(r"\bCPU \[([^\]]*)\]")
_EMC = re.compile(r"\bEMC_FREQ (\d+)%")
_TJ = re.compile(r"\btj@([\d.]+)C")


def parse_tegrastats(line: str) -> Sample:
    """One tegrastats line into a Sample. Unparseable fields stay None."""
    sample = Sample()

    match = _RAM.search(line)
    if match:
        sample.ram_mb, sample.ram_total_mb = int(match.group(1)), int(match.group(2))

    match = _LFB.search(line)
    if match:
        blocks, size = int(match.group(1)), int(match.group(2))
        # THE POOL, not the block size. The largest single block was 4 MB at
        # the 2026-08-18 idle baseline, so a 200 MB threshold on block SIZE
        # alone would fail an empty robot. Both are kept; the threshold is on
        # the pool.
        sample.lfb_pool_mb = blocks * size
        sample.lfb_block_mb = size

    match = _SWAP.search(line)
    if match:
        sample.swap_mb = int(match.group(1))

    match = _CPU.search(line)
    if match:
        cores: Dict[int, Optional[int]] = {}
        clocks: List[int] = []
        for index, field in enumerate(match.group(1).split(",")):
            field = field.strip()
            if not field or field == "off":
                cores[index] = None
                continue
            parts = field.split("%@")
            try:
                cores[index] = int(parts[0])
                if len(parts) > 1:
                    clocks.append(int(parts[1]))
            except ValueError:
                cores[index] = None
        sample.cores = cores
        sample.clocks_mhz = clocks

    match = _EMC.search(line)
    if match:
        sample.emc_pct = int(match.group(1))

    match = _TJ.search(line)
    if match:
        sample.tj_c = float(match.group(1))

    return sample


def longest_run(flags: Sequence[bool]) -> int:
    """The longest streak of True. "Sustained" means consecutive, not total.

    A budget that is over threshold for sixty scattered seconds across an hour
    is a different machine from one over threshold for sixty seconds straight,
    and only the second one is the failure this harness is looking for.
    """
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


class Verdict:
    def __init__(self, name: str, status: str, detail: str) -> None:
        self.name = name
        self.status = status  # PASS | WARN | FAIL | UNKNOWN
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Verdict({self.name!r}, {self.status!r})"


def judge(samples: Sequence[Sample], sustain_n: int = SUSTAIN_S) -> List[Verdict]:
    """The mechanical verdict. UNKNOWN where nothing was measured.

    UNKNOWN IS NOT PASS, and keeping them apart is the point: a run where
    tegrastats produced nothing must not read as a run where the budget was
    fine. That distinction is the whole reason this harness exists rather than
    somebody saying it felt fine.
    """
    out: List[Verdict] = []
    if not samples:
        return [Verdict("all", "UNKNOWN", "no samples were collected")]

    peak_ram = max([s.ram_mb for s in samples if s.ram_mb is not None] or [0]) or None
    if peak_ram is None:
        out.append(Verdict("memory", "UNKNOWN", "no RAM figures in the samples"))
    elif peak_ram > RAM_FAIL_MB:
        out.append(
            Verdict("memory", "FAIL", f"peak {peak_ram} MB > {RAM_FAIL_MB} MB")
        )
    else:
        out.append(
            Verdict("memory", "PASS", f"peak {peak_ram} MB of {RAM_TOTAL_MB} MB")
        )

    pools = [s.lfb_pool_mb for s in samples if s.lfb_pool_mb is not None]
    if not pools:
        out.append(Verdict("lfb pool", "UNKNOWN", "no lfb figures in the samples"))
    elif min(pools) < LFB_FAIL_MB:
        out.append(Verdict("lfb pool", "FAIL", f"min pool {min(pools)} MB"))
    else:
        out.append(Verdict("lfb pool", "PASS", f"min pool {min(pools)} MB"))

    swaps = [s.swap_mb for s in samples if s.swap_mb is not None]
    if swaps and max(swaps) > 0:
        out.append(Verdict("swap", "FAIL", f"peak {max(swaps)} MB — the budget is blown"))
    elif swaps:
        out.append(Verdict("swap", "PASS", "never touched"))

    for label, which in (("cores 5-7", BRIDGE_CORES), ("cores 6-7", UNPINNED_BRIDGE_CORES)):
        flags = []
        seen = False
        for sample in samples:
            value = sample.core_max(which)
            if value is None:
                flags.append(False)
                continue
            seen = True
            flags.append(value >= CORE_HI_FAIL_PCT)
        if not seen:
            out.append(Verdict(label, "UNKNOWN", "no CPU figures in the samples"))
            continue
        run = longest_run(flags)
        if run >= sustain_n:
            out.append(
                Verdict(label, "FAIL", f">= {CORE_HI_FAIL_PCT}% for {run} samples")
            )
        else:
            out.append(Verdict(label, "PASS", f"longest breach {run} samples"))

    temps = [s.tj_c for s in samples if s.tj_c is not None]
    if not temps:
        out.append(Verdict("thermal", "UNKNOWN", "no tj@ in the samples"))
    else:
        run = longest_run([t > TJ_FAIL_C for t in temps])
        if run >= sustain_n:
            out.append(Verdict("thermal", "FAIL", f"tj > {TJ_FAIL_C} C for {run} samples"))
        else:
            out.append(Verdict("thermal", "PASS", f"peak tj {max(temps)} C"))

    clocks = [s.mean_clock_mhz() for s in samples]
    clocks = [c for c in clocks if c is not None]
    if not clocks:
        out.append(Verdict("clocks", "UNKNOWN", "no CPU clocks in the samples"))
    elif min(clocks) < CLK_FAIL_MHZ:
        out.append(Verdict("clocks", "FAIL", f"min mean {min(clocks)} MHz"))
    else:
        out.append(Verdict("clocks", "PASS", f"min mean {min(clocks)} MHz"))

    emc = [s.emc_pct for s in samples if s.emc_pct is not None]
    if not emc:
        out.append(Verdict("EMC", "UNKNOWN", "no EMC_FREQ in the samples"))
    else:
        peak = max(emc)
        if longest_run([e > EMC_FAIL_PCT for e in emc]) >= sustain_n:
            out.append(Verdict("EMC", "FAIL", f"peak {peak}%"))
        elif peak > EMC_WARN_PCT:
            out.append(Verdict("EMC", "WARN", f"peak {peak}%"))
        else:
            out.append(Verdict("EMC", "PASS", f"peak {peak}%"))

    return out


def rate_verdict(topic: str, hz: Optional[float]) -> Verdict:
    """A topic's measured rate against its floor. None means it was not measured."""
    floor = RATE_MIN.get(topic)
    if hz is None:
        return Verdict(topic, "UNKNOWN", "not measured")
    if floor is None:
        return Verdict(topic, "PASS", f"{hz:.2f} Hz (no floor defined)")
    if hz < floor:
        return Verdict(topic, "FAIL", f"{hz:.2f} Hz < {floor} Hz")
    return Verdict(topic, "PASS", f"{hz:.2f} Hz")


def summarise(verdicts: Sequence[Verdict]) -> Tuple[int, int, int, str]:
    """(fails, warns, unknowns, the closing sentence)."""
    fails = sum(1 for v in verdicts if v.status == "FAIL")
    warns = sum(1 for v in verdicts if v.status == "WARN")
    unknowns = sum(1 for v in verdicts if v.status == "UNKNOWN")
    if fails:
        closing = f"{fails} FAIL(s). The stack does not fit as measured."
    elif unknowns:
        closing = (
            f"{unknowns} UNKNOWN(s) — this run did not measure everything, so it is not a pass."
        )
    elif warns:
        closing = f"{warns} WARN(s), no failures."
    else:
        closing = "Everything inside budget."
    return fails, warns, unknowns, closing


# --- the command ------------------------------------------------------------


def render(verdicts: Sequence[Verdict]) -> str:
    mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗", "UNKNOWN": "?"}
    lines = []
    for verdict in verdicts:
        lines.append(
            "  {} {:<22} {:<8}{}".format(
                mark.get(verdict.status, "?"), verdict.name, verdict.status, verdict.detail
            )
        )
    _, _, _, closing = summarise(verdicts)
    lines.append("")
    lines.append("  " + closing)
    return "\n".join(lines)


def threshold_banner(sustain_n: int) -> List[str]:
    """Printed BEFORE the run. Their whole value is being fixed in advance."""
    return [
        "Judging against these thresholds (fixed before the run):",
        f"  memory FAIL      peak RAM > {RAM_FAIL_MB} MB of {RAM_TOTAL_MB} MB (UNIFIED CPU+GPU)",
        f"                   or lfb free-block POOL < {LFB_FAIL_MB} MB",
        "                   or any swap at all",
        f"  cpu FAIL         any of cores 5-7 >= {CORE_HI_FAIL_PCT}% sustained (bridge + OS live there)",
        f"  thermal FAIL     tj@ > {TJ_FAIL_C} C sustained, or CPU clocks < {CLK_FAIL_MHZ} MHz",
        f"  EMC              WARN > {EMC_WARN_PCT}%, FAIL > {EMC_FAIL_PCT}% sustained",
        f'  "sustained"      {sustain_n} consecutive 1 Hz samples',
        "",
        "two readings that need saying out loud, so they are not re-interpreted later:",
        "  * 'lfb' is judged on the POOL (blocks x size). The largest single block",
        f"    was 4 MB at the 2026-08-18 idle baseline, so a {LFB_FAIL_MB} MB threshold on the",
        "    block SIZE alone would fail an empty robot. Both are reported.",
        "  * perception_up pins the vision container to core 5, so a core-5 breach",
        "    may be that pin rather than a budget breach. Cores 6-7 are separate.",
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    import subprocess
    import sys
    import time

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: measure.sh <label> [seconds]", file=sys.stderr)
        return 2
    label = args[0]
    try:
        duration = int(args[1]) if len(args) > 1 else 300
    except ValueError:
        print("seconds must be an integer", file=sys.stderr)
        return 2

    sustain_n = min(SUSTAIN_S, duration)
    print(f"Compute budget: {label}")
    print(f"duration: {duration}s   samples: 1 Hz")
    print()
    for line in threshold_banner(sustain_n):
        print(line)
    if duration < SUSTAIN_S:
        print()
        print(f"  ! duration {duration}s < {SUSTAIN_S}s: 'sustained' degrades to {sustain_n} samples")
        print("  ! a short run can only fail, never pass convincingly")
    print()

    samples: List[Sample] = []
    try:
        proc = subprocess.Popen(
            ["tegrastats", "--interval", "1000"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except OSError:
        print("  ? tegrastats is not available — nothing can be measured")
        print(render(judge([], sustain_n)))
        return 1

    deadline = time.monotonic() + duration
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            samples.append(parse_tegrastats(raw.decode("utf-8", "replace")))
            if time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        print("\n  interrupted — judging what was collected")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # tegrastats ignoring SIGTERM must not hold the harness open; the
            # samples are already collected and the verdict does not need it.
            proc.kill()

    print(f"  collected {len(samples)} samples")
    print()
    print(render(judge(samples, sustain_n)))
    # Non-zero only on FAIL. UNKNOWN is reported loudly and does not fail the
    # process: this is a measurement, and "we did not measure it" is a result
    # somebody has to read rather than a broken command.
    fails, _, _, _ = summarise(judge(samples, sustain_n))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
