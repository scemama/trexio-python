"""Contracts for direct NumPy exchange in sparse TREXIO calls."""

import gc

import numpy as np
import pytest

import trexio
import trexio.pytrexio as pytr
import trexio.trexio_gen as trexio_gen


@pytest.fixture
def sparse_handle(tmp_path):
    result = pytr.trexio_open(str(tmp_path / "sparse"), "w", pytr.TREXIO_TEXT)
    assert isinstance(result, list)
    handle, rc = result
    assert rc == trexio.TREXIO_SUCCESS
    assert pytr.trexio_write_ao_num(handle, 4096) == trexio.TREXIO_SUCCESS
    yield handle
    pytr.trexio_close(handle)


def test_sparse_roundtrip_returns_owned_numpy_arrays(sparse_handle):
    count = 256
    indices = np.arange(count * 4, dtype=np.int32)
    values = np.linspace(0.0, 1.0, count, dtype=np.float64)

    rc = pytr.trexio_write_safe_ao_2e_int_eri(
        sparse_handle, 0, count, indices, values
    )
    assert rc == trexio.TREXIO_SUCCESS

    result = pytr.trexio_read_safe_ao_2e_int_eri(
        sparse_handle, 0, count, count * 4, count
    )
    rc, read_count, read_indices, read_values = result
    assert (rc, read_count) == (trexio.TREXIO_SUCCESS, count)
    assert isinstance(read_indices, np.ndarray)
    assert isinstance(read_values, np.ndarray)
    assert read_indices.dtype == np.dtype(np.int32)
    assert read_values.dtype == np.dtype(np.float64)
    assert read_indices.shape == (count * 4,)
    assert read_values.shape == (count,)
    assert read_indices.flags.c_contiguous
    assert read_values.flags.c_contiguous

    # Each ndarray keeps its C allocation alive independently of the result
    # container and of later calls into TREXIO.
    del result
    gc.collect()
    np.testing.assert_array_equal(read_indices, indices)
    np.testing.assert_array_equal(read_values, values)


def test_sparse_write_rejects_implicit_array_copies(sparse_handle):
    indices = np.arange(40, dtype=np.int32)
    values = np.arange(10, dtype=np.float64)

    with pytest.raises(TypeError):
        pytr.trexio_write_safe_ao_2e_int_eri(
            sparse_handle, 0, 10, indices.astype(np.int64), values
        )
    with pytest.raises(TypeError):
        pytr.trexio_write_safe_ao_2e_int_eri(
            sparse_handle, 0, 10, indices.reshape(10, 4), values
        )
    with pytest.raises(TypeError):
        pytr.trexio_write_safe_ao_2e_int_eri(
            sparse_handle, 0, 10, indices[::2], values
        )
    with pytest.raises(ValueError, match="values must contain"):
        pytr.trexio_write_safe_ao_2e_int_eri(
            sparse_handle, 0, 10, indices, values[:5]
        )


def test_sparse_read_validates_allocation_sizes(sparse_handle):
    with pytest.raises(ValueError, match="buffer_size must be non-negative"):
        pytr.trexio_read_safe_ao_2e_int_eri(sparse_handle, 0, -1, 0, 0)
    with pytest.raises(ValueError, match="size_index must be at least"):
        pytr.trexio_read_safe_ao_2e_int_eri(sparse_handle, 0, 10, 5, 10)
    with pytest.raises(ValueError, match="size_value must be at least"):
        pytr.trexio_read_safe_ao_2e_int_eri(sparse_handle, 0, 10, 40, 5)


def test_public_sparse_writer_preserves_compatible_storage(monkeypatch):
    captured = {}

    def capture(_handle, _offset, _count, indices, values):
        captured["indices"] = indices
        captured["values"] = values
        return trexio.TREXIO_SUCCESS

    monkeypatch.setattr(pytr, "trexio_write_safe_ao_2e_int_eri", capture)

    class FileStub:
        pytrexio_s = object()

    indices = np.arange(40, dtype=np.int32).reshape(10, 4)
    values = np.arange(10, dtype=np.float64)
    trexio.write_ao_2e_int_eri(FileStub(), 0, 10, indices, values)

    assert captured["indices"].shape == (40,)
    assert captured["values"].shape == (10,)
    assert np.shares_memory(captured["indices"], indices)
    assert np.shares_memory(captured["values"], values)
