"""Phase 2 contract tests for the nanobind low-level module (``trexio.pytrexio``).

Pins the canonical low-level ABI (Plan README):
  * multi-output returns are **Python lists**, C return value **first**, then
    outputs in argument order; sparse output buffers are NumPy arrays;
  * ``trexio_open`` -> plain **int** rc on failure, else ``[handle, rc]``;
  * the handle exposes a readable ``back_end``;
  * ``trexio_string_of_error`` -> **str**;
  * int-rccalls (close/flush/info/set_state/delete_*) -> **int**;
  * ``has_backend`` -> Python **bool**.

Determinant/bitfield expectations are taken from the C front-end implementation
(prefix_front.c): ``to_bitfield_list(orb, N_int)`` returns SUCCESS (even a
phase-flipped determinant is rc-success; the bit list is always a plain
1-element list of one int for N_int=1); ``safe_to_orbital_list`` unpacks the
bits back; ``safe_to_orbital_list_up_dn`` treats the first half of the
determinant as up-spin and the second half as down-spin.

Run: python -m pytest tests/test_abi.py -v [--all]
"""
import gc
import os
import shutil

import pytest

import trexio.pytrexio as pytr

TEXT, HDF5 = 1, 0
BACKENDS = {"text": TEXT, "hdf5": HDF5}


def _cleanup(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _open(name, be):
    r = pytr.trexio_open(str(name), "w", be)
    if isinstance(r, int):
        pytest.fail(f"trexio_open failed: rc={r} -> {pytr.trexio_string_of_error(r)}")
    assert isinstance(r, list) and len(r) == 2
    h, rc = r
    assert rc == 0, f"open rc={rc} -> {pytr.trexio_string_of_error(rc)}"
    return h


@pytest.fixture
def h(tmp_path, backend):
    ext = "test_abi.h5" if backend == "hdf5" else "test_abi"
    name = tmp_path / ext
    _cleanup(str(name))
    handle = _open(name, BACKENDS[backend])
    yield handle
    try:
        pytr.trexio_close(handle)
    except Exception:
        pass
    _cleanup(str(name))


# ----------------------------- open/close ----------------------------------
def test_open_returns_list_and_back_end(tmp_path, backend):
    ext = "be.h5" if backend == "hdf5" else "be"
    h = _open(tmp_path / ext, BACKENDS[backend])
    assert h.back_end == BACKENDS[backend]
    assert pytr.trexio_close(h) == 0
    assert pytr.trexio_close(h) == -1  # closed -> TREXIO_FAILURE by contract


def test_get_back_end_function(tmp_path, backend):
    ext = "gb.h5" if backend == "hdf5" else "gb"
    h = _open(tmp_path / ext, BACKENDS[backend])
    assert pytr.trexio_get_back_end(h) == BACKENDS[backend]
    assert pytr.trexio_close(h) == 0
    assert pytr.trexio_get_back_end(h) == -1  # closed handle -> failure by contract


def test_open_failure_returns_int(tmp_path):
    r = pytr.trexio_open(str(tmp_path / "no_dir" / "x.h5"), "w", HDF5)
    assert isinstance(r, int), "failure must return a plain int rc"
    assert r != 0
    msg = pytr.trexio_string_of_error(r)
    assert isinstance(msg, str) and len(msg) > 0


def test_inquire_and_flush(tmp_path, backend, h):
    ext = ".h5" if backend == "hdf5" else ""
    assert pytr.trexio_inquire(str(tmp_path / ("absent" + ext))) == -1
    name_ext = "test_abi.h5" if backend == "hdf5" else "test_abi"
    assert pytr.trexio_inquire(str(tmp_path / name_ext)) == 0


def test_flush_and_info(tmp_path, backend, h):
    assert pytr.trexio_flush(h) == 0
    assert pytr.trexio_info() == 0


def test_state_roundtrip(h):
    rc, state = pytr.trexio_get_state(h)
    assert (rc, state) == (0, 0)
    assert pytr.trexio_set_state(h, 0) == 0


def test_has_backend(h):
    assert pytr.trexio_has_backend(1) is True  # TEXT always present
    assert pytr.trexio_has_back_end(1) is True


# ------------- back-end rebase (TREXIO 2.6.1+0102739, TREX-CoE#340) --------
def test_backend_constants_match_header():
    # module constants must track the linked library's header
    assert (pytr.TREXIO_HDF5, pytr.TREXIO_TEXT, pytr.TREXIO_AUTO, pytr.TREXIO_MEMORY) == (0, 1, 2, 3)
    assert pytr.TREXIO_INVALID_BACK_END == 4


def test_has_backend_full_range():
    # HDF5/TEXT/MEMORY are real back-ends; AUTO is a sentinel and INVALID is out of range
    for be in (pytr.TREXIO_HDF5, pytr.TREXIO_TEXT, pytr.TREXIO_MEMORY):
        assert pytr.trexio_has_backend(be) is True
    assert pytr.trexio_has_backend(pytr.TREXIO_AUTO) is False
    assert pytr.trexio_has_backend(pytr.TREXIO_INVALID_BACK_END) is False


def test_memory_backend_roundtrip(tmp_path):
    # MEMORY is now a first-class back-end (number 3); the file stays in memory
    r = pytr.trexio_open(str(tmp_path / "mem_ephemeral"), "w", pytr.TREXIO_MEMORY)
    assert isinstance(r, list) and len(r) == 2
    h, rc = r
    assert rc == 0
    assert h.back_end == pytr.TREXIO_MEMORY
    assert pytr.trexio_write_nucleus_num(h, 2) == 0
    rc, v = pytr.trexio_read_nucleus_num(h)
    assert (rc, v) == (0, 2)
    assert pytr.trexio_close(h) == 0


def test_memory_backend_read_mode_rejected():
    # post-2.6.1: MEMORY + 'r' is explicitly rejected with INVALID_ARG_2
    rc = pytr.trexio_open("ephemeral_path", "r", pytr.TREXIO_MEMORY)
    assert isinstance(rc, int) and rc == 2  # TREXIO_INVALID_ARG_2


def test_auto_open_requires_existing_file(tmp_path):
    # post-2.6.1: AUTO can no longer *create* a file -> TREXIO_FILE_ERROR (18)
    assert pytr.trexio_open(str(tmp_path / "ghost.h5"), "w", pytr.TREXIO_AUTO) == 18
    assert pytr.trexio_open(str(tmp_path / "ghost"), "w", pytr.TREXIO_AUTO) == 18


def test_auto_opens_existing_file(tmp_path, backend):
    # AUTO in 'r' mode still auto-detects an existing file
    ext = "auto.h5" if backend == "hdf5" else "auto"
    h = _open(tmp_path / ext, BACKENDS[backend])
    assert pytr.trexio_write_nucleus_num(h, 1) == 0
    assert pytr.trexio_close(h) == 0
    r = pytr.trexio_open(str(tmp_path / ext), "r", pytr.TREXIO_AUTO)
    assert isinstance(r, list) and len(r) == 2
    h2, rc = r
    assert rc == 0
    assert h2.back_end == BACKENDS[backend]
    assert pytr.trexio_close(h2) == 0


def test_string_of_error():
    assert pytr.trexio_string_of_error(11) == "Element absent"
    assert isinstance(pytr.trexio_string_of_error(15), str)


def test_one_based_flag(h):
    rc = pytr.trexio_set_one_based(h)  # allowed: may be SUCCESS on this line
    assert isinstance(rc, int)


def test_cp_roundtrip(tmp_path, backend):
    ext = ".h5" if backend == "hdf5" else ""
    src = tmp_path / ("cpc" + ext)
    dst = tmp_path / ("cpcd" + ext)
    h = _open(src, BACKENDS[backend])
    assert pytr.trexio_close(h) == 0
    rc = pytr.trexio_cp(str(src), str(dst))
    assert rc == 0
    assert pytr.trexio_inquire(str(src)) == 0
    assert pytr.trexio_inquire(str(dst)) == 0
    _cleanup(str(dst))


def test_get_int64_num_shape(h):
    r = pytr.trexio_get_int64_num(h)
    assert isinstance(r, list) and len(r) == 2
    assert isinstance(r[0], int) and isinstance(r[1], int)


def test_gc_is_safe(tmp_path):
    """Dropping the handle must not double-free / crash on GC."""
    name = tmp_path / "gc"
    r = _open(name, TEXT)
    del r
    gc.collect()  # finalizer runs trexio_close exactly once
    _cleanup(str(name))


# --------------------------- determinants / NAO -----------------------------
def test_bitfield_roundtrip():
    # 3 occupied orbitals in a single 64-bit int (N_int=1)
    r = pytr.trexio_to_bitfield_list([0, 1, 2], 1)
    assert isinstance(r, list) and r[0] == 0 and len(r[1]) == 1
    det = [int(x) for x in r[1]]
    assert det == [0b111]

    # safe_to_orbital_list: output buffer of size_max (unfilled tail zero);
    # the vendored API slices [0:occ_num] -- the binding must not pre-truncate.
    up = pytr.trexio_safe_to_orbital_list(1, det, 64)
    assert isinstance(up, list)
    assert up[0] == 0
    assert len(up[1]) == 64
    assert [int(x) for x in up[1][: up[2]]] == [0, 1, 2]
    assert up[2] == 3

    # up_dn form: first N_int bitfields up-spin, next N_int down-spin
    ud = pytr.trexio_safe_to_orbital_list_up_dn(1, [7, 0], 64, 64)
    assert isinstance(ud, list) and ud[0] == 0
    assert [int(x) for x in ud[1][: ud[3]]] == [0, 1, 2]
    assert list(ud[2][: ud[4]]) == []  # no down-spin orbitals
    assert ud[3] == 3 and ud[4] == 0

    # invalid arg (determinant shorter than 2*N_int) -> error code, still a list
    bad = pytr.trexio_safe_to_orbital_list_up_dn(1, [7], 64, 64)
    assert isinstance(bad, list) and bad[0] != 0


def test_convert_nao_radius():
    r = pytr.trexio_convert_nao_radius_py(1.5, [0.5, 1.0, 1.5, 2.0])
    assert isinstance(r, list) and len(r) == 2
    assert r[0] == 0
    import math
    assert isinstance(r[1], float)
    assert r[1] == pytest.approx(math.log(3.0) / math.log(2.0), rel=1e-12)


def test_evaluate_nao_radial_shapes():
    gs = [0]
    gz = [4]
    gr = [0.5, 1.0, 1.5, 2.0]
    # 4 spline coefficients per grid index (library requires 4*grid_size)
    it = [0.0, 1.0, 2.0, 3.0] + [0.0] * 12
    nm = [1.0]
    r = pytr.trexio_evaluate_nao_radial_py(0, 1.2, gs, gz, gr, it, nm)
    assert isinstance(r, list) and len(r) == 2
    assert r[0] == 0 and isinstance(r[1], float)
    assert abs(r[1]) < 1e9

    # NB: the C front end asserts amplitude_cnt > shell_num (debug builds
    # abort otherwise -- same contract as the SWIG module), so the caller
    # allocates one entry more than the number of evaluated shells.
    r2 = pytr.trexio_evaluate_nao_radial_all_py(
        1, [0], [0.0, 0.0, 0.0], gs, gz, gr, it, nm, 0.0, 0.0, 1.2, 2)
    assert isinstance(r2, list) and len(r2) == 2
    assert r2[0] == 0 and isinstance(r2[1], list)
    assert len(r2[1]) == 2 and isinstance(float(r2[1][0]), float)
