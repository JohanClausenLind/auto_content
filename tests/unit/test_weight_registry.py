"""The installable weight registry: every declared requirement resolves, and nothing is invented.

The whole point of models/weights.py is that a requirement the canvas shows as missing can be
installed from the browser. These tests are what keeps that true as workflows are added: a new
``models:`` entry in a workflow YAML with no registry source fails here rather than shipping a
card whose only advice is to read a README.
"""

from __future__ import annotations

import pytest

from content_factory.models.video_stack import VIDEO_STACK
from content_factory.models.weights import (
    SKILL_ENVS,
    WEIGHT_PACKAGES,
    HuggingFaceSource,
    ReleaseSource,
    WeightPackage,
    package_by_key,
    package_for_requirement,
    skill_env_by_key,
)
from content_factory.schemas.workflow_template import ModelRequirement
from content_factory.workflows.catalog import load_definitions


def test_every_declared_requirement_resolves_to_something_installable() -> None:
    unresolved: list[str] = []
    for template in load_definitions().values():
        for req in template.models:
            if req.kind == "skill":
                if skill_env_by_key(req.skill) is None:
                    unresolved.append(f"{template.id}: skill {req.skill}")
                continue
            package = package_for_requirement(req)
            if package is None:
                unresolved.append(f"{template.id}: {req.kind} {req.label}")
    assert unresolved == []


def test_keys_and_store_dirs_are_unique() -> None:
    keys = [p.key for p in WEIGHT_PACKAGES]
    assert len(keys) == len(set(keys))
    # Two families may share a repo (SeedVR2 3B and 7B) but never a store directory: they would
    # then be one directory whose presence check answers for both.
    dirs = [p.store_dir for p in WEIGHT_PACKAGES]
    assert len(dirs) == len(set(dirs))
    index = [(p.index_category, p.index_name) for p in WEIGHT_PACKAGES if p.index_category]
    assert len(index) == len(set(index))


def test_stack_keys_point_at_real_stack_entries() -> None:
    """Cross-check the two views of a family, so a rename cannot silently split them."""
    stack_keys = {entry.key for entry in VIDEO_STACK}
    for package in WEIGHT_PACKAGES:
        if package.stack_key:
            assert package.stack_key in stack_keys, package.key


def test_revisions_are_pinned_commits_not_branches() -> None:
    for package in WEIGHT_PACKAGES:
        for source in package.hf:
            assert len(source.revision) == 40, f"{package.key}: {source.repo_id}"
    with pytest.raises(ValueError):
        HuggingFaceSource(repo_id="acme/pack", revision="main")


def test_release_assets_must_be_https_on_an_allowlisted_host() -> None:
    for bad in (
        "http://github.com/acme/pack/releases/download/v1/w.pth",
        "https://evil.example/w.pth",
        "https://notgithub.com/acme/w.pth",
    ):
        with pytest.raises(ValueError):
            ReleaseSource(url=bad, dest="w.pth")
    ReleaseSource(url="https://github.com/a/b/releases/download/v1/w.pth", dest="w.pth")


def test_a_package_declares_a_source_or_says_why_it_cannot() -> None:
    for package in WEIGHT_PACKAGES:
        assert package.installable != bool(package.manual)
    # Neither: the registry would show an install button that cannot do anything.
    with pytest.raises(ValueError):
        WeightPackage(
            key="nothing",
            name="Nothing",
            purpose="x",
            store_dir="nothing",
            license="mit",
            approx_bytes=0,
            provides=({"store_rel": "w.pth"},),  # type: ignore[arg-type]
        )


def test_manual_families_are_the_documented_two() -> None:
    """A new un-installable family is a decision, not an accident: RIFE's weights are on Google
    Drive and SAM 3.1 needs Meta's approval, and both say so in the UI."""
    assert {p.key for p in WEIGHT_PACKAGES if p.manual} == {"rife"}
    assert package_by_key("sam-3.1") is not None
    assert package_by_key("sam-3.1").gating == "manual"  # type: ignore[union-attr]


def test_comfy_requirements_match_on_folder_as_well_as_filename() -> None:
    req = ModelRequirement(
        kind="comfy",
        label="LTX-2.5 transformer",
        folder="diffusion_models",
        filename="ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
    )
    assert package_for_requirement(req) is not None
    wrong_folder = req.model_copy(update={"folder": "loras"})
    assert package_for_requirement(wrong_folder) is None


def test_path_requirements_match_the_store_directory_or_an_alias() -> None:
    hidream = ModelRequirement(
        kind="path",
        label="HiDream-O1 weights",
        path_includes="hidream",
        filename="model-00001-of-00008.safetensors",
    )
    resolved = package_for_requirement(hidream)
    assert resolved is not None and resolved.key == "hidream-o1"
    unknown = ModelRequirement(kind="path", label="?", path_includes="nothing-here")
    assert package_for_requirement(unknown) is None


def test_skill_envs_exist_on_disk_as_uv_projects() -> None:
    from content_factory.models.weight_install import REPO_ROOT

    for env in SKILL_ENVS:
        assert (REPO_ROOT / env.skill / "pyproject.toml").is_file(), env.skill
