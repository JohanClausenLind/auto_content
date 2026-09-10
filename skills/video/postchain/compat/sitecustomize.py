"""Compatibility shims for the GIMM-VFI checkout. On `PYTHONPATH`, so `site` imports it first.

`external/GIMM-VFI` is a 2024 research checkout and its forward-warp kernel is built with
`cupy.cuda.compile_with_cache`, which CuPy removed in 13.0. The obvious fix — pin CuPy to the last
12.x — does not work here: the venv's torch is a CUDA 13 build and ships `libcudart.so.13`, so
`cupy-cuda12x==12.3.0` installs and then fails to import with "libcudart.so.12: cannot open shared
object file". Downgrading torch to reach a removed CuPy API is the wrong trade, so the API comes
back instead: `cupy.RawModule` does exactly what `compile_with_cache` did, caches per device the
same way, and has been there since CuPy 8.

Measured: without this, `video-finish` died at `interpolate` with "module 'cupy.cuda' has no
attribute 'compile_with_cache'" — 21 minutes into the run, because SeedVR2 upscales first.

Nothing here runs unless CuPy is installed and actually missing the attribute, so a future CuPy
that brings it back, or an env without CuPy at all, is untouched.
"""

from __future__ import annotations

import os


def _restore_compile_with_cache() -> None:
    try:
        import cupy
    except Exception:
        # No CuPy, or a CuPy that cannot see a GPU. Either way this shim has nothing to do, and a
        # sitecustomize that raises takes the whole interpreter down with it.
        return
    if hasattr(cupy.cuda, "compile_with_cache"):
        return

    def compile_with_cache(source: str, options: tuple[str, ...] = (), *_a: object, **kw: object):
        """The pre-13 signature, narrowed to what callers in these checkouts actually pass.

        Include options are filtered to directories that exist. softsplat builds them from
        `CUDA_HOME`, which it sets from `cupy.cuda.get_cuda_path()`; on a wheel-only install that
        path is real but its `include` may not be, and nvrtc treats a missing `-I` as an error
        rather than a warning.
        """
        kept: list[str] = []
        for opt in options:
            head = opt[: len("-I")]
            if head == "-I":
                path = opt[len("-I") :].strip()
                if path and os.path.isdir(path):
                    kept.append(f"-I{path}")
                continue
            kept.append(opt)
        backend = kw.get("backend", "nvrtc")
        return cupy.RawModule(code=source, options=tuple(kept), backend=str(backend))

    cupy.cuda.compile_with_cache = compile_with_cache  # type: ignore[attr-defined]


_restore_compile_with_cache()
