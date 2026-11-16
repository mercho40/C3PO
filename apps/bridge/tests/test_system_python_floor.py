"""The modules that run on the robot's SYSTEM python must keep working there.

`c3po_health` and `c3po_preflight` are shims over these, and both are typed
by a human at exactly the moment the stack is broken — a venv that failed to
sync is precisely when somebody runs a health check. So they promise, in their
own docstrings, to need nothing but python 3.8 and the standard library.

Nothing enforced that promise until this file. It was noticed the honest way:
`health.py` was edited to report the lidar ring, the edit happened to stay
inside the floor, and nothing anywhere would have said so if it had not.

TWO PROPERTIES, both cheap:

  * the SYNTAX parses under 3.8, so a walrus-in-a-comprehension or a match
    statement borrowed from the rest of the codebase fails here rather than on
    the Jetson;
  * every import — including the function-local ones these modules use
    deliberately — resolves to the standard library or to another module on
    this list.

WHAT THIS CANNOT CATCH, stated so nobody trusts it further than it goes:
`ast.parse(feature_version=...)` rejects newer SYNTAX only. A 3.9+ stdlib
*method* (`str.removeprefix`, `dict |= dict`) parses cleanly and would still
fail on the robot. The import check is the load-bearing half; the syntax check
is a cheap extra.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "bridge"

#: Modules reached by `python3 -m bridge.<name>` on the robot's system
#: interpreter, plus everything they import from this package.
SYSTEM_PYTHON_MODULES = {
    "bridge.health": SRC / "health.py",
    "bridge.preflight": SRC / "preflight.py",
    # Not a shim entry point of its own — preflight imports it, which puts it
    # inside the same promise. A dependency of a stdlib-only module is part of
    # the stdlib-only module.
    "bridge.env_file": SRC / "env_file.py",
}

FLOOR = (3, 8)


def _imports(tree: ast.AST) -> set:
    """Every module named by an import, at any depth. Function-local included.

    These files import `os`, `subprocess` and `urllib.request` INSIDE functions
    on purpose, so a top-level-only scan would check almost nothing.
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module; relative imports stay in-package
            # and are covered by the package check below.
            if node.module and node.level == 0:
                found.add(node.module)
    return found


def test_the_module_list_points_at_real_files():
    """Guards the test: a renamed file would make every case below vanish."""
    assert SYSTEM_PYTHON_MODULES
    for name, path in SYSTEM_PYTHON_MODULES.items():
        assert path.exists(), "{} is listed here but {} does not exist".format(name, path)


@pytest.mark.parametrize("name", sorted(SYSTEM_PYTHON_MODULES))
def test_parses_under_python_38(name):
    path = SYSTEM_PYTHON_MODULES[name]
    try:
        ast.parse(path.read_text(), filename=str(path), feature_version=FLOOR)
    except SyntaxError as exc:
        pytest.fail(
            "{} uses syntax newer than python {}.{}: {}\n"
            "It runs on the robot's SYSTEM interpreter, which is where a health "
            "check has to work when nothing else does.".format(
                name, FLOOR[0], FLOOR[1], exc
            )
        )


@pytest.mark.parametrize("name", sorted(SYSTEM_PYTHON_MODULES))
def test_imports_nothing_outside_the_standard_library(name):
    path = SYSTEM_PYTHON_MODULES[name]
    tree = ast.parse(path.read_text(), filename=str(path))

    offenders = []
    for module in sorted(_imports(tree)):
        root = module.split(".")[0]
        if root == "bridge":
            # In-package is allowed only for modules held to the same promise.
            if module not in SYSTEM_PYTHON_MODULES:
                offenders.append(
                    "{} (in-package, and NOT itself stdlib-only)".format(module)
                )
            continue
        if root not in sys.stdlib_module_names:
            offenders.append(module)

    assert not offenders, (
        "{} imports {} — but it runs on the robot's system python, which has "
        "no venv and none of this project's dependencies.\n"
        "Either drop the import, or add the module to SYSTEM_PYTHON_MODULES "
        "and hold it to the same floor.".format(name, ", ".join(offenders))
    )


def test_the_bridge_package_init_stays_docstring_only():
    """`import bridge.health` must not drag in the whole package.

    health.py's docstring says it "imports nothing from `bridge` beyond the
    package's own docstring-only `__init__`". If someone adds
    `from .mcp_server import ...` there, running `c3po_health` on the robot
    imports FastMCP, structlog and the Unitree SDK — on an interpreter that has
    none of them — and the health check dies exactly when it is needed.
    """
    init = SRC / "__init__.py"
    tree = ast.parse(init.read_text(), filename=str(init))
    imports = _imports(tree)
    assert not imports, "bridge/__init__.py must import nothing, found: {}".format(
        sorted(imports)
    )
