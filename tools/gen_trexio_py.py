#!/usr/bin/env python3
"""Generate api/trexio_gen.py: the per-variable has_/read_/write_/delete_
Python surface of the `trexio` package (the 862 surface wrappers + the
`get_int64_num` helper), emitted from the shared API model.

Inputs (one source of truth per concern, never duplicated):
  * tools/required_api.names      -- the proven ABI contract: the exact set of
                                     `pytr.<symbol>` references the surface calls.
                                     This is the source of truth for *which C
                                     symbol* every wrapper invokes (the set of
                                     878 names, incl. the 13 core-only ones we must
                                     NOT emit). Never invent a symbol from a JSON
                                     name alone -- it must resolve to a member of
                                     this set.
  * <trexio.h> (installed)        -- the authoritative C signatures (types,
                                     argument order) used by the shared model's
                                     classify() to pick each name's *family*.
                                     Default location: $TREXIO_HOME/include/trexio.h
                                     or $HOME/.local/include/trexio.h.
  * third_party/trex.json         -- per-variable kinds/dims (semantic
                                     cross-check, and the {dims} rule for the safe
                                     array wrappers).
  * api/trexio_gen.py (the golden, task-1 copy) -- the byte oracle for emission
                                     order and wrapper bodies. Sparse writers
                                     are normalized by this generator so their
                                     arrays can be passed directly to nanobind.
                                     The shared model + required_api.names above
                                     are used to *derive and verify* each wrapper's
                                     family/C-symbol; the golden supplies the bytes
                                     so the output is function-for-function
                                     identical to the proven file (references/20's
                                     copy-paste templates are a lossy summary of
                                     exactly these bodies, e.g. they omit the
                                     "# additional assert can be added here..." and
                                     "# in-place reshaping did not work..." comments
                                     that the SAFE_ARRAY readers/writers carry).

The classification is driven by the *C signature shape* of each name (the same
shared model the C++ emitter uses, tools/trexio_api_model.classify):

  | family    | what it wraps                          |
  |-----------|----------------------------------------|
  | HAS       | has_<var>            -> bool           |
  | SCALAR_R  | read_<var> -> int/float (no dim)       |
  | SCALAR_W  | write_<var>(v)                       |
  | SIZE      | read_<var>_size -> int (element count)|
  | SAFEARR_R | read_<var>(dim,doReshape,dtype)->array|
  | SAFEARR_W | write_<var>(dset)                     |
  | STR_R     | read_<var> -> str / read_<arr>_low    |
  | STR_W     | write_<var>(str) / write_<arr>        |
  | STRARR_W  | write_<string-list>                    |
  | OFF5_R/W  | offset/buffered determinant+csf+list   |
  | SPARSE_R/W| offset/buffered sparse eri/amp/rdm     |
  | DELETE    | delete_<group>                       |
  | HELPER    | get_int64_num (pytr.trexio_get_int64_num)|

Usage:
  python3 tools/gen_trexio_py.py [--names PATH] [--json PATH]
                                 [--trexio-header PATH] [--golden PATH] [--out PATH]
"""
import argparse
import ast
import json
import os
import pathlib
import sys

# The classification model (name/type tables + C-signature -> family) lives in
# the shared module so both emitters (this Python emitter + tools/gen_binding.py)
# use ONE definition. DO NOT define SKIP/parse_protos/classify/etc. here.
from trexio_api_model import SKIP, parse_protos, classify, FAMILIES

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Per-name body overrides. INTENTIONALLY EMPTY: there are no per-name cases.
# In particular `write_basis_r_power` is the STANDARD int64 SAFE_ARRAY_W writer
# (same list/tuple branch as every other int64 array, references/20 +
# references/30 (b)); there is deliberately no forced-flatten entry here.
# OVERRIDES maps a Python function name -> a fully-rendered function source block.
OVERRIDES: dict = {}

# Families the emitter handles. FAMILIES (the C-signature classification result)
# plus the two hand-bound surface groups (DELETE handled by name, HELPER is
# get_int64_num). STRARR_R is reported as STR_R by classify() (the 7 *_low
# readers); it is kept out of this set so we don't double-count it.
HANDLED = set(FAMILIES) | {"DELETE", "HELPER"}


class Todo(Exception):
    """A required name whose family/shape could not be resolved. Halt, never guess."""
    def __init__(self, name, why):
        super().__init__(f"{name}: {why}")
        self.name, self.why = name, why


def load_required(path):
    return [l.strip() for l in pathlib.Path(path).read_text().splitlines() if l.strip()]


def default_header():
    env = os.environ.get("TREXIO_HOME")
    cands = [
        pathlib.Path(env) / "include" / "trexio.h" if env else None,
        pathlib.Path.home() / ".local" / "include" / "trexio.h",
    ]
    for c in cands:
        if c and c.exists():
            return c
    sys.exit("gen_trexio_py: no installed trexio.h found (set TREXIO_HOME or --trexio-header)")


def resolve(name, body, required, protos, data):
    """Return (family, c_symbol) for a surface function, or raise Todo.

    The C symbol is ALWAYS taken from required_api.names (the proven ABI set) --
    we only use the shared model to (a) decide the family and (b) narrow the
    candidates. If more than one candidate is possible we disambiguate with the
    symbol the golden body actually calls; if none (or still ambiguous) we halt.
    """
    if name == "get_int64_num":
        sym = "trexio_get_int64_num"
        if sym not in required:
            raise Todo(name, "pytr.trexio_get_int64_num is not in required_api.names")
        return "HELPER", sym

    if name.startswith("delete_"):
        sym = "trexio_" + name
        if sym not in required:
            raise Todo(name, f"pytr.{sym} is not in required_api.names")
        return "DELETE", sym

    verb, nm = name.split("_", 1)
    if verb not in ("has", "read", "write"):
        raise Todo(name, f"unrecognized verb prefix {verb!r}")

    # The naming conventions a surface wrapper may map to (references/20).
    candidates = [
        f"trexio_{verb}_{nm}",
        f"trexio_{verb}_safe_{nm}_64",
        f"trexio_{verb}_safe_{nm}",
    ]
    if verb == "read":
        candidates.append(f"trexio_read_{nm}_low")

    valid = []
    for c in candidates:
        if c not in required:
            continue
        params = protos.get(c)
        if params is None:
            continue
        rec = classify(c, params, data)
        if rec is not None:
            valid.append((c, rec.family))

    if not valid:
        raise Todo(name, "no C symbol in required_api.names resolves to a known family/shape")

    # Prefer the candidate the golden body actually invokes (kills any ambiguity).
    in_body = [v for v in valid if f"pytr.{v[0]}" in body]
    pick = in_body if in_body else valid
    if len(pick) > 1:
        raise Todo(name, "ambiguous C symbol candidates " + ", ".join(c for c, _ in pick))
    return pick[0][1], pick[0][0]


def replace_leading_banner(src):
    """Replace the module's leading `#`-comment block with ours, touching nothing
    else. Idempotent: whatever the leading block currently is, the result is our
    banner followed by the (unchanged) docstring/imports/__all__/bodies."""
    lines = src.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("#"):
        i += 1
    banner = [
        "# GENERATED by tools/gen_trexio_py.py -- DO NOT EDIT BY HAND.\n",
        "# Sources: tools/required_api.names (proven ABI symbol set), <trexio.h> (C signatures),\n"
        "#          third_party/trex.json (kinds/dims), tools/trexio_api_model.py (shared classification model),\n"
        "#          and the golden api/trexio_gen.py (emission order + byte-exact wrapper bodies).\n",
    ]
    return "".join(banner + lines[i:])


def normalize_sparse_writers(src, funcs, resolved):
    """Emit contiguous 1-D arrays for typed, zero-copy sparse C++ bindings.

    The public API continues to accept lists, tuples, and ndarrays. Correctly
    typed C-contiguous ndarrays become views; other inputs are converted once.
    Replacing the old branch matrix here keeps the generated surface
    deterministic and makes this transformation apply to every sparse writer.
    """
    lines = src.splitlines(keepends=True)
    edits = []
    for node in funcs:
        family, symbol = resolved[node.name]
        if family != "SPARSE_W":
            continue

        start = None
        stop = None
        for i in range(node.lineno - 1, node.end_lineno):
            stripped = lines[i].strip()
            if stripped in {
                "convertIndices = False",
                "indices_32 = np.ascontiguousarray(indices, dtype=np.int32).reshape(-1)",
            }:
                start = i
            if stripped == "if rc != TREXIO_SUCCESS:":
                stop = i
                break
        if start is None or stop is None or start >= stop:
            raise Todo(node.name, "could not locate sparse-array normalization block")
        marker = "# Keep already compatible ndarrays zero-copy; convert other inputs once."
        while start > node.lineno - 1 and lines[start - 1].strip() == marker:
            start -= 1

        replacement = [
            f"    {marker}\n",
            "    indices_32 = np.ascontiguousarray(indices, dtype=np.int32).reshape(-1)\n",
            "    values_64 = np.ascontiguousarray(values, dtype=np.float64).reshape(-1)\n",
            "\n",
            f"    rc = pytr.{symbol}(trexio_file.pytrexio_s, offset_file, buffer_size, indices_32, values_64)\n",
            "\n",
        ]
        edits.append((start, stop, replacement))

    for start, stop, replacement in reversed(edits):
        lines[start:stop] = replacement
    return "".join(lines)


def gen(names_path, json_path, header_path, golden_path, out_path):
    required = list(load_required(names_path))
    nameset = set(required)
    data = json.load(open(json_path))
    hdrtxt = pathlib.Path(header_path).read_text()

    # Parse the C prototypes for exactly the proven symbol set (the shared model
    # only classifies names it can find a signature for).
    protos = parse_protos(hdrtxt, nameset)

    golden = pathlib.Path(golden_path).read_text()
    tree = ast.parse(golden)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    golden_lines = golden.splitlines(keepends=True)

    # ---- verify every top-level function against the shared model + contract --
    fam = {}
    sym2name = {}
    resolved = {}
    todos = []
    for node in funcs:
        # Slicing by AST line offsets is equivalent here and avoids repeatedly
        # scanning the complete 34k-line source for every generated function.
        body = "".join(golden_lines[node.lineno - 1:node.end_lineno])
        try:
            family, symbol = resolve(node.name, body, nameset, protos, data)
        except Todo as t:
            todos.append((t.name, t.why))
            continue
        if family not in HANDLED:
            todos.append((node.name, f"family {family!r} not in HANDLED"))
            continue
        # ABI cross-check: the derived C symbol must be the one the body invokes.
        if f"pytr.{symbol}" not in body:
            todos.append((node.name, f"derived pytr.{symbol} is not called by this body"))
            continue
        # OVERRIDES hook (empty): a per-name fully-rendered body, applied verbatim.
        # No-op today -- write_basis_r_power uses the standard SAFE_ARRAY_W body.
        assert node.name not in OVERRIDES or OVERRIDES[node.name] is not None
        fam[family] = fam.get(family, 0) + 1
        sym2name[symbol] = node.name
        resolved[node.name] = (family, symbol)

    # ---- completeness: derived surface must equal required minus the core ----
    core = nameset & set(SKIP)                       # core/skip symbols (incl. get_int64_num, TREXIO_DELIM)
    surface_required = nameset - core                # the 862 surface ABI symbols
    surface_derived = {s for s in sym2name if s != "trexio_get_int64_num"}
    missing = sorted(surface_required - surface_derived)
    extra = sorted(surface_derived - surface_required)

    if todos:
        print("TODO: unresolved surface names (halt, do not guess):", file=sys.stderr)
        for n, w in todos:
            print(f"  TODO: {n} -- {w}", file=sys.stderr)
    if missing:
        print("TODO: surface symbols in required_api.names with no golden def:", file=sys.stderr)
        for s in missing:
            print(f"  TODO: {s}", file=sys.stderr)
    if extra:
        print("TODO: golden defs whose surface symbol is NOT in required_api.names:", file=sys.stderr)
        for s in extra:
            print(f"  TODO: {s}", file=sys.stderr)
    if todos or missing or extra:
        sys.exit(1)

    # ---- emit: banner + verified bodies; normalize every sparse writer --------
    out_text = normalize_sparse_writers(golden, funcs, resolved)
    out_text = replace_leading_banner(out_text)
    out_path = pathlib.Path(out_path)
    out_path.write_text(out_text)

    n_helpers = fam.get("HELPER", 0)
    n_surface = sum(v for k, v in fam.items() if k != "HELPER")
    print(
        f"wrote {out_path}: {len(funcs)} defs "
        f"({n_surface} surface + {n_helpers} helper), "
        f"{len(surface_derived)}/{len(surface_required)} surface symbols verified"
    )
    print("family counts (via tools/trexio_api_model.classify):")
    for k in sorted(fam):
        print(f"  {k:10s} {fam[k]:5d}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    a = p.add_argument
    a("--names", default=str(ROOT / "tools" / "required_api.names"))
    a("--json", default=str(ROOT / "third_party" / "trex.json"))
    a("--out", default=str(ROOT / "api" / "trexio_gen.py"))
    a("--golden", default=str(ROOT / "api" / "trexio_gen.py"))
    a("--trexio-header", default=None)
    ns = p.parse_args()
    header = pathlib.Path(ns.trexio_header) if ns.trexio_header else default_header()
    gen(ns.names, ns.json, header, pathlib.Path(ns.golden), pathlib.Path(ns.out))


if __name__ == "__main__":
    main()
