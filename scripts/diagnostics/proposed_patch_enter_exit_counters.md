# Proposed patch (NOT APPLIED): per-edge, per-time-bin `n_enter`/`n_exit`

## Why

`run_nomad_traffic_validation.py`'s `timebin_metrics`/`mass_conservation`
outputs can only approximate per-time-bin mass conservation (occupancy
deltas between consecutive snapshots) because NOMAD does not currently
record real entry/exit counts anywhere:

- `external/nomad/src/output/geojson_writer.cpp` (`GeoJsonWriter::on_snapshot`)
  emits only instantaneous `count` (occupancy at the snapshot instant),
  `flow_veh_h` (a derived Little's-Law estimate, capacity-capped), and
  `capacity_veh_h` -- no `n_enter`/`n_exit`.
- `external/nomad/src/traffic/queue_model.cpp` (`QueueTrafficModel::on_enter`
  / `on_exit`) already fire on every real agent entry/exit event but only
  update `occupancy_count`/`last_exit_time` -- they don't accumulate
  per-bin counters anywhere `GeoJsonWriter` could read from.

A real `outflow_veh_h = n_exit / bin_duration_s * 3600` (measured, not a
Little's-Law estimate) would let a rebuilt fundamental diagram use genuine
throughput instead of an occupancy-based rate estimate, and would make
per-time-bin mass conservation exact
(`occupancy(t) = occupancy(t-1) + n_enter - n_exit`) instead of an
occupancy-delta proxy.

## Scope (minimal, additive, no behavior change to existing outputs)

1. `external/nomad/include/nomad/traffic/traffic_model.hpp`: add two
   `std::atomic<uint32_t>` fields to `LinkState` --
   `enter_count_since_snapshot`, `exit_count_since_snapshot`. Existing
   fields (`occupancy`, `travel_time_s`, `outflow_rate`, `congestion_ema`)
   are untouched.
2. `external/nomad/src/traffic/queue_model.cpp`:
   - `on_enter`: `states_[e].enter_count_since_snapshot.fetch_add(1, ...)`
     alongside the existing `++lq.occupancy_count`.
   - `on_exit`: `states_[e].exit_count_since_snapshot.fetch_add(1, ...)`
     alongside the existing `--lq.occupancy_count`.
   - No change to `on_enter`/`on_exit`'s existing return values, the BPR
     formula, or `flow_cap_per_s` gating -- purely additive counters.
3. `external/nomad/src/output/geojson_writer.cpp` (`on_snapshot`): read
   `enter_count_since_snapshot`/`exit_count_since_snapshot`, emit as new
   JSON properties `"n_enter"`/`"n_exit"` (additive -- existing properties
   unchanged), then reset both counters to 0 (`.store(0, ...)`) so the next
   snapshot only reflects the following window. This reset must happen
   AFTER writing, and only for edges actually visited in the loop (the
   current loop already `continue`s past `occ == 0` edges -- an edge that
   had entries+exits but ended the bin at occupancy 0 would currently be
   skipped entirely by that `if (occ == 0.0f) continue;` guard, silently
   dropping its enter/exit counts. This edge case needs the same guard
   relaxed to `if (occ == 0.0f && enter == 0 && exit == 0) continue;` so a
   fully-cleared edge with real activity that bin still gets a row.
4. `src/green_mobility/nomad_wrapper/car_snapshots.py` (`load_car_bins`):
   read `p["n_enter"]`/`p["n_exit"]` (falling back to `None`/NaN if an older
   snapshot format lacks them, so this stays backward-compatible with
   already-generated results/ directories), add:
   - `n_enter`, `n_exit` (as read)
   - `outflow_veh_h = n_exit * 3600.0 / bin_duration_s` (a real measured
     rate, replacing the need for `flow_veh_h_uncapped` in a rebuilt
     fundamental diagram)
   - `mass_balance_residual = (occ_this_bin - occ_prev_bin) - (n_enter - n_exit)`
     (should be ~0 modulo the snapshot-instant approximation already
     documented in the module docstring)

## Required tests before merge (per repo protocol -- dedicated branch, no
auto-merge, user reviews/authorizes)

- `external/nomad/tests/unit/test_traffic.cpp`: new case asserting
  `enter_count_since_snapshot`/`exit_count_since_snapshot` increment
  exactly once per real `on_enter`/`on_exit` call and reset to 0 after a
  simulated "read" (mirroring how GeoJsonWriter would consume them),
  using the same synthetic single/multi-edge fixtures already in that
  file. Full existing Catch2 suite (63 cases at last count) must still
  pass unchanged.
- A small real-network regression: re-run Palma's existing
  `weekday_full_car` baseline before/after, confirm `n_enter`/`n_exit`
  sums are internally consistent (`sum(n_enter) - sum(n_exit) ==
  final_on_link` at the end of the run, from the same run_stats already
  parsed by `parse_nomad_cli_stats`) and that all pre-existing columns
  (`occupancy_veh`, `flow_veh_h`, `travel_time_ratio`, ...) are
  byte-for-byte unchanged (this patch must not alter existing behavior).

## Not done here

This patch is a description only. No NOMAD source file has been modified by
`run_nomad_traffic_validation.py` or this document. Applying it requires the
same dedicated-branch, no-auto-push/merge protocol already used for the
Barcelona NetworkCleaner fix earlier in this project -- explicit
authorization needed before any C++ change lands.
