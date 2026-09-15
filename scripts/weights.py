#!/usr/bin/env python
"""The weight store: what is installed here, what is on the other hosts, and pinning more."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from content_factory.models.weight_install import (  # noqa: E402
    ensure_index_link,
    hf_binary,
    package_status,
    weight_store,
)
from content_factory.models.weights import WEIGHT_PACKAGES, WeightPackage  # noqa: E402

DEFAULT_HOSTS = ("nova@100.82.150.94",)
"""Every other machine that runs this pipeline. Weights travel by rsync; *code* travels by git —
push here, pull there. Mixing the two is how a host ends up running last week's stage against this
week's weights."""


def _package(key: str) -> WeightPackage:
    for package in WEIGHT_PACKAGES:
        if package.key == key:
            return package
    known = ", ".join(sorted(p.key for p in WEIGHT_PACKAGES))
    raise SystemExit(f"unknown family {key!r}\nknown: {known}")


def _remote_sizes(host: str, store: Path, package: WeightPackage) -> dict[str, int] | None:
    """Byte sizes of the family's declared files on ``host``, or None when it cannot be reached."""
    target = store / package.store_dir
    script = (
        "import json,os,sys\n"
        f"root={str(target)!r}\n"
        f"rels={json.dumps([f.store_rel for f in package.provides])}\n"
        "out={}\n"
        "for r in rels:\n"
        "    p=os.path.join(root,r)\n"
        "    out[r]=os.path.getsize(p) if os.path.isfile(p) else 0\n"
        "print(json.dumps(out))\n"
    )
    try:
        done = subprocess.run(
            # ssh and python3 off PATH on purpose: the remote host's own, not this one's.
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return None


def cmd_status(args: argparse.Namespace) -> int:
    store = weight_store()
    keys = args.keys or [p.key for p in WEIGHT_PACKAGES]
    hosts = list(args.host or DEFAULT_HOSTS)
    print(f"store {store}   hosts: {', '.join(hosts) or '(local only)'}\n")
    worst = 0
    for key in keys:
        package = _package(key)
        local = package_status(package, store=store).as_dict()
        here = local["state"]
        gib = local["bytes_on_disk"] / 2**30
        line = f"{key:<24} {here:<10} {gib:7.2f} GiB  {local['index_state']}"
        for host in hosts:
            sizes = _remote_sizes(host, store, package)
            name = host.split("@")[0]
            if sizes is None:
                line += f"  {name}:unreachable"
                continue
            want = {f.store_rel: f.min_bytes for f in package.provides}
            missing = [r for r, m in want.items() if sizes.get(r, 0) < m]
            if missing:
                line += f"  {name}:MISSING({len(missing)}/{len(want)})"
                worst = max(worst, 1)
            else:
                line += f"  {name}:ok"
        print(line)
        if here != "ready":
            worst = max(worst, 1)
    return worst


def cmd_install(args: argparse.Namespace) -> int:
    """Download a family exactly as the registry pins it, then create the models/ index link."""
    package = _package(args.key)
    store = weight_store()
    target = store / package.store_dir
    if package.manual:
        raise SystemExit(f"{package.key} cannot be installed from here: {package.manual}")
    if not package.hf:
        raise SystemExit(f"{package.key} has no Hugging Face source; see weights.py")

    hf = hf_binary()
    for source in package.hf:
        dest = target / source.dest_subdir if source.dest_subdir else target
        dest.mkdir(parents=True, exist_ok=True)
        cmd = [
            hf,
            "download",
            source.repo_id,
            "--revision",
            source.revision,
            "--local-dir",
            str(dest),
        ]
        for name in source.files:
            cmd.insert(3, name)
        for pattern in source.include:
            cmd += ["--include", pattern]
        print(f"$ {' '.join(cmd)}", flush=True)
        done = subprocess.run(cmd, check=False)
        if done.returncode != 0:
            return done.returncode
        if source.strip_prefix:
            nested = dest / source.strip_prefix
            if nested.is_dir():
                for item in nested.iterdir():
                    item.rename(dest / item.name)
                nested.rmdir()

    print("index link:", ensure_index_link(package, store=store))
    state = package_status(package, store=store).as_dict()["state"]
    print("state:", state)
    return 0 if state == "ready" else 1


def cmd_mirror(args: argparse.Namespace) -> int:
    """Copy a family to the other hosts at the identical absolute path."""
    package = _package(args.key)
    store = weight_store()
    source = store / package.store_dir
    if not source.is_dir():
        raise SystemExit(f"{source} is not here yet; run `install {package.key}` first")

    rc = 0
    for host in args.host or DEFAULT_HOSTS:
        # Trailing-slash-free source plus a parent destination keeps the directory NAME, which is
        # what makes the two paths identical. `rsync src/ dst/` would splat the contents instead.
        cmd = [
            "rsync",
            "-a",
            "--partial",
            "--info=progress2,stats2",
            "--exclude=.cache/",
            str(source),
            f"{host}:{store}/",
        ]
        if args.dry_run:
            cmd.insert(1, "--dry-run")
        print(f"$ {' '.join(cmd)}", flush=True)
        done = subprocess.run(cmd, check=False)
        rc = rc or done.returncode
        if done.returncode == 0 and not args.dry_run:
            sizes = _remote_sizes(host, store, package)
            want = {f.store_rel: f.min_bytes for f in package.provides}
            if sizes is None:
                print(f"{host}: copied, but could not verify (ssh/python3 unavailable)")
                rc = rc or 1
            else:
                short = [r for r, m in want.items() if sizes.get(r, 0) < m]
                print(f"{host}: verified {len(want) - len(short)}/{len(want)} declared files")
                rc = rc or (1 if short else 0)
    return rc


def cmd_pin(args: argparse.Namespace) -> int:
    """Resolve a Hub repo to a sha and a size, and print the registry stanza to paste."""
    url = f"https://huggingface.co/api/models/{args.repo_id}?blobs=true"
    with urllib.request.urlopen(url, timeout=30) as response:
        meta = json.load(response)
    total = sum(s.get("size") or 0 for s in meta.get("siblings", []))
    card = meta.get("cardData") or {}
    store_dir = args.repo_id.split("/")[-1].lower().replace("_", "-")
    biggest = sorted(
        ((s.get("size") or 0, s["rfilename"]) for s in meta.get("siblings", [])), reverse=True
    )[:4]
    provides = chr(10).join(
        f'            ProvidedFile(store_rel="{n}", min_bytes={int(size * 0.98)}),'
        for size, n in biggest
        if size
    )
    print(
        f"""    WeightPackage(
        key="{store_dir}",
        name="{meta.get("modelId", args.repo_id)}",
        purpose="TODO: what this repo uses it for, in one line",
        store_dir="{store_dir}",
        license="{card.get("license") or "TODO: read the model card"}",
        approx_bytes={total}_
        provides=(
{provides}
        ),
        hf=(
            HuggingFaceSource(
                repo_id="{args.repo_id}",
                revision="{meta.get("sha")}",
            ),
        ),
        index_category="TODO",
        index_name="TODO",
    ),""".replace(f"{total}_", f"{total:_}")
    )
    if meta.get("gated"):
        print(
            f"\n# NOTE: gated={meta['gated']} — accept the terms on the Hub first", file=sys.stderr
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="The weight store: what is here, what is on the other box, and pinning more."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="what is installed here and on the other hosts")
    p.add_argument("keys", nargs="*")
    p.add_argument("--host", action="append", help="repeatable; default nova")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("install", help="download a family exactly as the registry pins it")
    p.add_argument("key")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("mirror", help="copy a family to the other hosts at the identical path")
    p.add_argument("key")
    p.add_argument("--host", action="append", help="repeatable; default nova")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_mirror)

    p = sub.add_parser("pin", help="resolve a Hub repo to a sha + size and print the stanza")
    p.add_argument("repo_id")
    p.set_defaults(func=cmd_pin)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
