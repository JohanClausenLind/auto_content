"""Local model inventory (18.8 companion): what model files exist on disk.

Roots come only from configuration — the settings workspace, comfy-cli's own default workspace
(when `managed_by_comfy_cli`), and explicitly configured extra roots. Listing is the whole job:
nothing here downloads, moves, hashes or opens model files, and no caller-supplied path is ever
scanned, so there is no traversal surface.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from content_factory.config import Settings

MODEL_EXTENSIONS = frozenset(
    {".safetensors", ".sft", ".gguf", ".ckpt", ".pt", ".pth", ".onnx", ".bin"}
)
MAX_DEPTH = 3
COMFY_CLI_CONFIG = Path.home() / ".config" / "comfy-cli" / "config.ini"


@dataclass(frozen=True)
class ModelRoot:
    path: Path
    source: str  # settings | comfy-cli | extra
    exists: bool


@dataclass(frozen=True)
class ModelFile:
    """One model file. `kind` is the directory family ComfyUI sorts by (loras, vae, ...)."""

    kind: str
    filename: str
    relative_path: str  # posix path relative to its root
    size_bytes: int
    root: str


def comfy_cli_default_workspace(config_file: Path = COMFY_CLI_CONFIG) -> Path | None:
    """The workspace comfy-cli itself would use, read from its ini. None when unset/unreadable."""
    try:
        parser = configparser.ConfigParser()
        if not parser.read(config_file):
            return None
        workspace = parser["DEFAULT"].get("default_workspace", "").strip()
    except (configparser.Error, KeyError, OSError):
        return None
    return Path(workspace).expanduser() if workspace else None


def resolve_roots(
    settings: Settings, *, comfy_cli_config: Path = COMFY_CLI_CONFIG
) -> list[ModelRoot]:
    """Configured roots, deduplicated, in precedence order."""
    candidates: list[tuple[str, Path]] = [
        ("settings", Path(settings.comfyui.workspace).expanduser() / "models")
    ]
    if settings.comfyui.managed_by_comfy_cli:
        cli_workspace = comfy_cli_default_workspace(comfy_cli_config)
        if cli_workspace is not None:
            candidates.append(("comfy-cli", cli_workspace / "models"))
    for extra in settings.comfyui.extra_model_roots:
        candidates.append(("extra", Path(extra).expanduser()))

    roots: list[ModelRoot] = []
    seen: set[Path] = set()
    for source, path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(ModelRoot(path=resolved, source=source, exists=resolved.is_dir()))
    return roots


def scan_models(roots: list[ModelRoot]) -> list[ModelFile]:
    """Every model file under the given roots, at most MAX_DEPTH levels deep. Names and sizes
    only; deterministic order (root order, then path)."""
    files: list[ModelFile] = []
    for root in roots:
        if not root.exists:
            continue
        found: list[ModelFile] = []
        for dirpath, dirnames, filenames in os.walk(root.path, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root.path)
            depth = 0 if rel_dir == Path(".") else len(rel_dir.parts)
            # Prune: never descend into hidden/VCS dirs or below the depth cap.
            dirnames[:] = (
                []
                if depth >= MAX_DEPTH
                else sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
            )
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in MODEL_EXTENSIONS:
                    continue
                full = Path(dirpath) / name
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                rel = rel_dir / name if depth else Path(name)
                kind = rel.parts[0] if len(rel.parts) > 1 else root.path.name
                found.append(
                    ModelFile(
                        kind=kind,
                        filename=name,
                        relative_path=rel.as_posix(),
                        size_bytes=size,
                        root=str(root.path),
                    )
                )
        files.extend(sorted(found, key=lambda f: f.relative_path))
    return files
