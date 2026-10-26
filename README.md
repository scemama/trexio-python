# trexio (Python)

Python API of the [TREXIO](https://trex-coe.github.io/trexio) library — a **nanobind**
low-level extension that **replaces the SWIG-generated `pytrexio` binding**.
Drop-in: `import trexio` behaves exactly as before.

The package ships the TREXIO Python API (`api/trexio.py`, the hand-written
**core** — see `PROVENANCE.md`) on top of a single compiled extension:

```
trexio/
├── __init__.py          # the TREXIO Python API (public surface: File, open/close,
│                        #   read_*/write_*/has_*, to_bitfield_list, …)
├── _version.py
└── pytrexio.<tag>.so    # nanobind low-level module (importable as `trexio.pytrexio`)
```

## Requirements

- Python ≥ 3.9 (tested on 3.12)
- A C++17 toolchain and CMake ≥ 3.21 (for building from source)
- **Installed TREXIO ≥ 2.6** C library (`libtrexio`) with the back ends you use:
  - `module load trexio` (HPC), or a local prefix
    (`./configure --prefix=…; make && make install` from the TREXIO sources),
  - the HDF5 back end only if that `libtrexio` was built with HDF5,
  - the MEMORY back end on TREXIO line 2.6.1+ (built with it — default).
- `numpy`

## Install

```sh
pip install ./dist/trexio-0.1.0-*.whl     # prebuilt wheel (preferred)
# or build from source (build deps handled by scikit-build-core):
pip install .
```

The TREXIO C library must be **findable at build time** (`include/trexio.h`
plus `lib*/libtrexio.so`): set `TREXIO_HOME`, `CMAKE_PREFIX_PATH`, or use the
default `~/.local`.

At runtime the extension needs to load `libtrexio.so` — export
`LD_LIBRARY_PATH` / `module load trexio` as you would for any TREXIO client.

## Usage (unchanged public API)

```python
import numpy as np
import trexio

with trexio.File("benzene.h5", "w", trexio.TREXIO_HDF5) as trexio_file:
  trexio.write_nucleus_num(trexio_file, 6)
  trexio.write_nucleus_repulsion(trexio_file, 1)
  trexio.write_nucleus_coord(trexio_file, np.zeros((6, 3)))

with trexio.File("benzene.h5", "r", trexio.TREXIO_HDF5) as trexio_file:
  r = trexio.read_nucleus_num(trexio_file)        # 6
  coords = trexio.read_nucleus_coord(trexio_file)  # numpy array (6, 3)
```

Back ends: `trexio.TREXIO_HDF5`, `trexio.TREXIO_TEXT`, `trexio.TREXIO_AUTO`
(existing files), `trexio.TREXIO_MEMORY` (in-process; no file needed).

Errors raise `trexio.Error` carrying the TREXIO exit code;
`trexio.string_of_error(rc)` decodes it.

Low-level access (drop-in stand-in for the old SWIG `pytrexio.pytrexio`):
`import trexio.pytrexio` — same names, same return shapes (Python list with
the C return code first), same `File` handle object. Sparse read buffers are
NumPy arrays backed directly by the C output allocation.

## What changed vs the SWIG release

- **Nothing user-visible** in the public API: same names, same `File` class,
  same constants, same return shapes (return code first, then results as
  Python objects in the shapes `api/trexio.py` was written against). Sparse
  buffers remain NumPy arrays instead of being expanded into Python lists.
- The low-level module is one compiled nanobind extension instead of a SWIG
  `.so` plus generated `.py` shims — the `pytrexio` Python shim files are gone
  (parity-verified in `tests/evidence/`).
- `handle.back_end` reads the true back end from the library via
  `trexio_get_back_end` (TREXIO 2.6 line) — useful after a `TREXIO_AUTO` open.
- The truly-vendored files (`api/pytrexio/*`, `third_party/trex.json`) are
  re-vendored on a documented step (`PROVENANCE.md`) when the TREXIO API line
  changes; the core `api/trexio.py` is hand-maintained, and the per-variable
  surface is **regenerated** — `api/trexio_gen.py` via `tools/gen_trexio_py.py`
  (Python) and `src/generated_bindings.cpp` via `tools/gen_binding.py` (C++).

## Development

```sh
python -m pytest tests/ -v --all        # text+hdf5+memory suites
python tools/gen_binding.py             # regenerate src/generated_bindings.cpp
python tools/check_api_symbols.py --verify
python tools/parity_api.py              # public-API contract dump (parity tooling)
```

**File discipline** (full breakdown in [`docs/package-layout.md`](docs/package-layout.md)):
`api/trexio.py` is the **hand-written core** (lifecycle, NAO, bitfield/orbital
helpers) — maintained by hand. `api/trexio_gen.py` (the 862 `has_/read_/write_/delete_`
wrappers) is **generated** by `tools/gen_trexio_py.py` — regenerate, never hand-edit.
`src/generated_bindings.cpp` and `tools/required_api.names` are also **generated** —
regenerate, never hand-edit. `api/pytrexio/*` and `third_party/trex.json` are
**vendored** — re-copy them on a new TREXIO release. `api/trexio/_base.py`,
`src/core.cpp`, `src/bind_helpers.hpp`, `tools/gen_binding.py`, and `tests/` are
**hand-maintained** — edit only when the release requires it.

Loop: regenerate → `pip install -v .` → test. The runbook for staying current
when TREXIO itself changes (incl. "which files to touch, by case") is
`docs/updating-with-trexio.md`; migration-from-SWIG notes are in
`docs/migration-from-swig.md`; source layout of the shipped package is in
`docs/package-layout.md`. Packaging evidence and the SWIG↔nanobind parity record
live in `tests/evidence/`.

## Provenance

- TREXIO (C library + original Python API + tests): BSD-3-Clause,
  TREX Center of Excellence — see `LICENSE`, `PROVENANCE.md`
  (vendored files and versions), and the pinned `third_party/trex.json`.
- nanobind: MIT; scikit-build-core: MIT (build-time only).
