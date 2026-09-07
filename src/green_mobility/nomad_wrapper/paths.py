"""Locate the NOMAD submodule's build artifacts.

NOMAD is built once, out-of-tree, following external/nomad/README.md ("Quick
start" section). This module only *finds* what that build produced — it never
invokes cmake/make itself (that's a deliberate, real build step the operator
runs, not something to trigger silently from a data pipeline).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from green_mobility.config import NOMAD_DIR

NOMAD_BUILD_DIR = NOMAD_DIR / "build"
NOMAD_CLI_BIN = NOMAD_BUILD_DIR / "nomad_cli"
NOMAD_PYTHON_DIR = NOMAD_DIR / "python"

# Conda env whose libstdc++.so.6 must win the process-wide SONAME race —
# see the LD_PRELOAD note in require_nomad_python() below.
_NOMAD_CONDA_LIBSTDCXX = Path.home() / ".conda" / "envs" / "nomad" / "lib" / "libstdc++.so.6"


class NomadNotBuiltError(RuntimeError):
    pass


def require_nomad_cli() -> Path:
    """Return the path to the built nomad_cli binary, or raise with the exact
    build command from external/nomad/README.md "Quick start"."""
    if not NOMAD_CLI_BIN.exists():
        raise NomadNotBuiltError(
            f"nomad_cli not found at {NOMAD_CLI_BIN}. Build it first:\n\n"
            f"  conda activate nomad   # see {NOMAD_DIR / 'environment.yml'}\n"
            f"  /usr/bin/cmake -B {NOMAD_BUILD_DIR} -DCMAKE_BUILD_TYPE=Release "
            f"-DNOMAD_BUILD_TESTS=ON -DCMAKE_PREFIX_PATH=\"$HOME/miniconda3/envs/nomad\" -Wno-dev\n"
            f"  cmake --build {NOMAD_BUILD_DIR} -j$(nproc)\n"
        )
    return NOMAD_CLI_BIN


def require_nomad_python() -> None:
    """Put external/nomad/python on sys.path and confirm `_nomad_core` (the
    compiled pybind11 module) actually imports, raising a build hint if not.
    Needed only by flows.py (hook-based per-mode instrumentation); the CLI
    subprocess path in runner.py does not need this.
    """
    py_dir = str(NOMAD_PYTHON_DIR)
    if py_dir not in sys.path:
        sys.path.insert(0, py_dir)
    try:
        import nomad._nomad_core  # noqa: F401
    except ImportError as exc:
        if "CXXABI" in str(exc) or "GLIBCXX" in str(exc):
            # Not a missing build: pandas/pyarrow (imported by this same
            # process — flows.py imports pandas at module level) loads the
            # SYSTEM libstdc++.so.6 first via its own dependency chain. Once
            # a library's SONAME is loaded, ld.so reuses that instance for
            # every later NEEDED reference to "libstdc++.so.6" — including
            # NOMAD's libtbb, which requires a newer libstdc++ than the
            # system one provides — regardless of _nomad_core.so's own
            # correct RUNPATH. Preloading the conda env's own libstdc++
            # first wins that race. Confirmed reproducible/fixable in this
            # exact form; see nomad_wrapper README notes.
            raise NomadNotBuiltError(
                f"NOMAD's Python module failed to import due to a libstdc++ "
                f"version conflict, not a missing build:\n{exc}\n\n"
                "pandas/pyarrow (imported earlier in this same process) load "
                "the system libstdc++.so.6 first; NOMAD's bundled libtbb "
                "needs a newer one from the 'nomad' conda env, but once a "
                "library's SONAME is loaded the dynamic linker won't load a "
                "second copy — regardless of _nomad_core.so's own RPATH. Fix: "
                "preload the conda env's libstdc++ before starting Python:\n\n"
                f"  LD_PRELOAD={_NOMAD_CONDA_LIBSTDCXX} python ...\n"
            ) from exc
        raise NomadNotBuiltError(
            f"NOMAD's Python module (_nomad_core) is not importable from {NOMAD_PYTHON_DIR}. "
            f"Build NOMAD with -DNOMAD_BUILD_PYTHON=ON (the default) — see "
            f"{NOMAD_DIR / 'README.md'} 'Quick start'."
        ) from exc


def run_nomad_cli(config_json: Path) -> subprocess.CompletedProcess:
    """Runs nomad_cli and stashes peak RSS on the result as
    `.peak_rss_kb` (via resource.getrusage(RUSAGE_CHILDREN), Linux-only,
    accurate as long as this process hasn't reaped other children first —
    true for the `gm run` CLI path). None if unavailable (e.g. non-Linux)."""
    import resource

    binary = require_nomad_cli()
    result = subprocess.run(
        [str(binary), "--config", str(config_json)],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        result.peak_rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (AttributeError, OSError):
        result.peak_rss_kb = None
    return result
