"""Compatibility shims for the GIMM-VFI checkout."""

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
        """The pre-13 signature, narrowed to what callers in these checkouts actually pass."""
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
