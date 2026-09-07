"""Confirms the flows.py capability guard fails loudly (rather than silently
returning empty/zero data) when NOMAD's Python bindings can't route agents —
see nomad_wrapper/flows.py module docstring for why this check exists.
Skipped if NOMAD hasn't been built at all in this environment (nothing to
guard yet); if it HAS been built, the guard must still fire until
bindings/pynomad.cpp exposes set_router/set_traffic_model.
"""
import pytest

from green_mobility.nomad_wrapper.flows import NomadCapabilityError, assert_flow_extraction_available
from green_mobility.nomad_wrapper.paths import NomadNotBuiltError


def test_capability_guard_fails_loudly_not_silently():
    try:
        assert_flow_extraction_available()
    except NomadNotBuiltError:
        pytest.skip("NOMAD not built in this environment — nothing to guard yet")
    except NomadCapabilityError:
        return  # expected: current bindings don't expose set_router/set_traffic_model
    else:
        pytest.fail(
            "assert_flow_extraction_available() passed — either NOMAD's bindings "
            "now expose set_router/set_traffic_model (update this test and "
            "flows.py's docstring/README accordingly) or the guard has a bug."
        )
