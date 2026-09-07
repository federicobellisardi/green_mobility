"""Thin wrapper around the NOMAD simulation engine (external/nomad).

NOMAD is never modified or reimplemented here — this package only
materializes its JSON scenario configs, shells out to its CLI binary, and
(for per-mode flow extraction) drives its pybind11 Python bindings through
their public, documented API (register_hook / OdMatrixDemand.from_csv).
See docs in each module and README.md "Known NOMAD gaps" for why.
"""
