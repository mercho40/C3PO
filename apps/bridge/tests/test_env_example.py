"""Every environment variable the bridge reads is documented in `.env.example`.

`.env.example` is where an operator learns what a robot can be told. In
2026-11 the code read 80 names and the file documented 44, and one of the 36
missing, STOP_EVERYTHING_DAMP, changes what the e-stop does. A variable nobody
can find is a behaviour nobody can choose, or one somebody set years ago and
nobody can explain.

So this scans `src/` with `ast` for every read:

  * `os.environ.get("X")`, `os.getenv("X")`, `os.environ["X"]`, `"X" in os.environ`
  * a helper that passes its own parameter to one of those (`_env_float("X", 1.0)`,
    `_env_flag("X")`) — found by shape, not by name, so a new helper is covered
  * a module constant as the key (`ALLOW_PICK_ENV = "C3PO_ALLOW_PICK"`), and a loop over a
    tuple of them (`for k in CAMERA_ENV`)

and requires each name to appear in `.env.example`, set or commented out. A key
it cannot resolve to a name fails too: name it with a constant.

Structural on purpose: reads source text, imports nothing from the bridge.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
SRC = BRIDGE / "src" / "bridge"
EXAMPLE = BRIDGE / ".env.example"

#: Read by the bridge's code but never set in apps/bridge/.env, so not listed there.
#: Each one says who sets it instead.
NOT_IN_EXAMPLE = {
    # preflight.py runs on the operator's Mac and checks the OTHER apps' config:
    # these belong to apps/back/.env and apps/web/.env (and their .env.example).
    "BRIDGE_URL": "apps/back/.env, read by preflight.py",
    "PUBLIC_API_URL": "apps/web/.env, read by preflight.py",
    "PUBLIC_ROBOT_CAM_URL": "apps/web/.env, read by preflight.py",
    "NODE_ENV": "Bun's own .env.<NODE_ENV> convention, read by preflight.py",
    "C3PO_ROBOT_HOST": "the Mac's shell (scripts/robot_session.sh), read by preflight.py",
    # Set by the shell shims around `python3 -m bridge.<module>`.
    "C3PO_DIR": "scripts/headset_preflight.sh and scripts/robot/_common.sh",
    # Written by the bridge itself from its DDS settings before cyclonedds loads.
    "CYCLONEDDS_URI": "sdk/connection.py, from DDS_INTERFACE and ROBOT_HOST",
}

#: Files whose environment reads are generic by design, so the key is no one name.
DYNAMIC_READS = {
    "src/bridge/env_file.py": "applies .env itself: every name in the file",
}

_ENV_CALLS = {("os", "environ", "get"), ("environ", "get"), ("os", "getenv"), ("getenv",)}


def _dotted(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _is_environ(node: ast.AST) -> bool:
    return _dotted(node) in {("os", "environ"), ("environ",)}


def _env_key(node: ast.AST) -> ast.AST | None:
    """The key expression of a direct environment read, else None."""
    if isinstance(node, ast.Call) and _dotted(node.func) in _ENV_CALLS and node.args:
        return node.args[0]
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        if isinstance(node.ctx, ast.Load):
            return node.slice
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.In | ast.NotIn)
        and _is_environ(node.comparators[0])
    ):
        return node.left
    return None


def _strings(node: ast.AST | None) -> list[str] | None:
    """A string literal, or a tuple/list of them, as a list."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List):
        out = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        if len(out) == len(node.elts) and all(isinstance(s, str) for s in out):
            return out  # type: ignore[return-value]
    return None


def _module_constants(tree: ast.Module) -> dict[str, list[str]]:
    consts: dict[str, list[str]] = {}
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        found = _strings(value)
        if found is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                consts[t.id] = found
    return consts


def _loop_bindings(tree: ast.AST, consts: dict[str, list[str]]) -> dict[str, list[str]]:
    """`for k in CONST` / `for k, d in zip(CONST, ...)`: the names `k` can take."""
    bound: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For | ast.comprehension):
            target, it = node.target, node.iter
        else:
            continue
        if isinstance(target, ast.Name) and isinstance(it, ast.Name) and it.id in consts:
            bound.setdefault(target.id, []).extend(consts[it.id])
        elif (
            isinstance(target, ast.Tuple)
            and isinstance(it, ast.Call)
            and _dotted(it.func) == ("zip",)
        ):
            for elt, arg in zip(target.elts, it.args, strict=False):
                if isinstance(elt, ast.Name) and isinstance(arg, ast.Name) and arg.id in consts:
                    bound.setdefault(elt.id, []).extend(consts[arg.id])
    return bound


def _parsed() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(), filename=str(p))) for p in sorted(SRC.rglob("*.py"))]


def _helpers(trees: list[tuple[Path, ast.Module]]) -> set[str]:
    """Functions that read the environment with one of their own parameters as the key."""
    names = set()
    for _, tree in trees:
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            for node in ast.walk(fn):
                key = _env_key(node)
                if isinstance(key, ast.Name) and key.id in params:
                    names.add(fn.name)
    return names


@functools.cache
def scan() -> tuple[dict[str, set[str]], list[str]]:
    """name -> files that read it, and every read whose key is not a resolvable name."""
    trees = _parsed()
    helpers = _helpers(trees)
    found: dict[str, set[str]] = {}
    unresolved: list[str] = []
    for path, tree in trees:
        rel = str(path.relative_to(BRIDGE))
        consts = _module_constants(tree)
        loops = _loop_bindings(tree, consts)
        helper_params: set[str] = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.name in helpers:
                helper_params |= {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for node in ast.walk(tree):
            key = _env_key(node)
            if (
                key is None
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in helpers
                and node.args
            ):
                key = node.args[0]
            if key is None:
                continue
            names = _strings(key)
            if names is None and isinstance(key, ast.Name):
                names = consts.get(key.id) or loops.get(key.id)
                if names is None and key.id in helper_params:
                    continue  # the helper's own body; its callers are scanned
            if not names:
                if rel in DYNAMIC_READS:
                    continue
                unresolved.append(f"{rel}:{getattr(node, 'lineno', '?')}")
                continue
            for name in names:
                found.setdefault(name, set()).add(rel)
    return found, unresolved


def documented() -> set[str]:
    """Names assigned in .env.example, set or commented out.

    A commented one is a whole line, `# X=` or `# X=default`: prose that happens to
    start with `X=…` (`# X=1 for pick; set these…`) documents nothing.
    """
    text = EXAMPLE.read_text()
    live = re.findall(r"^\s*([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)
    commented = re.findall(r"^\s*#\s*([A-Z][A-Z0-9_]*)=\S*\s*$", text, re.MULTILINE)
    return set(live) | set(commented)


def test_the_scan_finds_every_kind_of_read():
    # Guards the scanner: one name per shape it claims to handle.
    found, _ = scan()
    assert "SIM_MODE" in found  # os.environ.get("…")
    assert "SAFETY_MAX_LINEAR_MPS" in found  # a helper, safety._env_float
    assert "C3PO_ALLOW_PICK" in found  # a module constant as the key
    assert "C3PO_CAMERA_PITCH_DEG" in found  # a loop over a tuple constant
    assert "STOP_EVERYTHING_DAMP" in found


def test_every_env_read_has_a_resolvable_name():
    _, unresolved = scan()
    assert not unresolved, (
        "these environment reads have a key this test cannot name; use a string "
        "literal or a module constant: " + ", ".join(unresolved)
    )


def test_every_env_var_the_bridge_reads_is_in_env_example():
    found, _ = scan()
    missing = sorted(set(found) - documented() - set(NOT_IN_EXAMPLE))
    assert not missing, "\n".join(
        ["read in src/ but not in apps/bridge/.env.example (add it, with its default):"]
        + [f"  {name}  ({', '.join(sorted(found[name]))})" for name in missing]
    )


def test_the_allowlist_has_no_stale_entries():
    found, _ = scan()
    stale = sorted(set(NOT_IN_EXAMPLE) - set(found))
    assert not stale, f"no longer read anywhere in src/: {stale}"
    both = sorted(set(NOT_IN_EXAMPLE) & documented())
    assert not both, f"allowlisted AND in .env.example, drop one: {both}"
