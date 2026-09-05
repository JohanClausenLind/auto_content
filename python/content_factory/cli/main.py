"""Typer CLI entry point: ``content-factory``.

Commands are added phase by phase; each one calls the same service layer as the API.
"""

from __future__ import annotations

import json
import os
from typing import cast

import typer
from rich.console import Console

from content_factory import __version__

app = typer.Typer(
    name="content-factory",
    help="Content Factory — local-first content production and distribution.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """Check services, runtimes, GPU, configuration, and explain problems in plain language."""
    from content_factory.doctor import run_doctor

    report = run_doctor()
    if as_json:
        console.print_json(json.dumps(report.model_dump(mode="json")))
    else:
        report.render(console)
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default from config; loopback)."),
    port: int | None = typer.Option(None, help="Port (default from config)."),
    reload: bool = typer.Option(False, help="Auto-reload for development."),
) -> None:
    """Run the API server (loopback by default; use `tailscale serve` for tailnet access)."""
    import uvicorn

    from content_factory.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "content_factory.api.app:app",
        host=host or settings.bind,
        port=port or settings.port,
        reload=reload,
        log_level="info",
    )


@app.command()
def bootstrap(
    owner: str = typer.Option("operator", help="Owner account username."),
    password: str | None = typer.Option(None, help="Owner password (generated if omitted)."),
    demo: bool = typer.Option(True, help="Seed two demo workspaces."),
) -> None:
    """Create the owner account and demo workspaces (idempotent). Prints a generated password once."""  # noqa: E501
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.services.bootstrap import bootstrap as _bootstrap

    async def _run() -> None:
        async with session_scope() as db:
            res = await _bootstrap(db, owner_username=owner, password=password, demo=demo)
        console.print(f"owner account: [bold]{res.owner_username}[/]")
        if res.generated_password:
            console.print(f"generated password (shown once): [bold red]{res.generated_password}[/]")
        console.print(f"workspaces created: {list(res.workspaces) or 'none (already present)'}")

    asyncio.run(_run())


@app.command("model-check")
def model_check(
    prompt: str = typer.Argument(
        "Name three qualities of a good video hook.", help="Question to send."
    ),
) -> None:
    """Prove the local model path end to end: route via the default catalog (local_only, zero
    cloud candidates), call Ollama, validate the structured reply. No cloud egress, ever."""
    from pydantic import BaseModel, Field

    from content_factory.budgets.ledger import Cap, Scope
    from content_factory.models.catalog import build_gateway
    from content_factory.models.gateway import ExecutionPausedError, GatewayError
    from content_factory.schemas.skills import (
        PRESETS,
        CostEstimator,
        ExecutionLocation,
        ExecutorType,
        Lifecycle,
        SkillManifest,
        SkillPermissions,
    )

    class Reply(BaseModel):
        answer: str = Field(min_length=1)

    skill = SkillManifest(
        skill_id="ops.model_check",
        version="1.0.0",
        status=Lifecycle.active,
        purpose="operator smoke check of the local model path",
        input_schema="EditBatch",
        output_schema="EditBatch",
        executor=ExecutorType.model_role,
        implementation_ref="content_factory.cli.main:model_check",
        permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.local_cpu),
        required_models=("local_structured", "local_structured_small"),
        permissions=SkillPermissions(network_egress=False),
        license_evidence="Apache-2.0",
        cost=CostEstimator(kind="per_token", usd=0),
        timeout_seconds=300,
        max_retries=1,
    )
    gateway = build_gateway()
    gateway.ledger.set_cap(Cap(scope=Scope.monthly, key="ops", limit_usd=1.0))
    try:
        result = gateway.complete_structured(
            skill,
            PRESETS["private_local"],
            Reply,
            [{"role": "user", "content": prompt}],
            budget_scopes=[(Scope.monthly, "ops")],
        )
    except (ExecutionPausedError, GatewayError) as exc:
        console.print(f"[red]model check failed:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"model: [bold]{result.model_alias}[/] (attempts={result.attempts})")
    console.print(
        f"tokens: {result.input_tokens} in / {result.output_tokens} out · "
        f"{result.elapsed_s:.1f}s · ${result.actual_usd:.4f}"
    )
    console.print(f"answer: {result.value.answer}")  # type: ignore[attr-defined]


@app.command("reset-password")
def reset_password(
    username: str = typer.Argument("operator", help="Account to reset."),
    password: str | None = typer.Option(
        None,
        help="New password (AVOID: lands in shell history and ps; prefer --prompt or generated).",
    ),
    prompt: bool = typer.Option(
        False,
        "--prompt",
        help="Type the new password with hidden input (never touches argv, history, or ps).",
    ),
) -> None:
    """Reset a local account's password (direct DB access; run on the server as the operator).
    Prints the new password once — Argon2id hashes cannot be recovered, only replaced."""
    import asyncio
    import secrets as _secrets

    if prompt:
        password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)

    from sqlalchemy import select

    from content_factory.auth import passwords as _passwords
    from content_factory.db.models import Account
    from content_factory.db.session import session_scope

    # One predicate for both the generate and the echo decision: an empty --password must
    # not silently install a random secret while claiming "not echoed" (unrecoverable lockout).
    generated = not password
    new_password = password if password else _secrets.token_urlsafe(18)

    async def _run() -> None:
        async with session_scope() as db:
            acct = (
                await db.execute(select(Account).where(Account.username == username))
            ).scalar_one_or_none()
            if acct is None:
                console.print(f"[red]no account named {username!r}[/]")
                raise typer.Exit(1)
            acct.password_hash = _passwords.hash_password(new_password)
            has_totp = acct.totp_secret is not None
        console.print(f"account: [bold]{username}[/]")
        if generated:
            console.print(f"new password (shown once): [bold red]{new_password}[/]")
        else:
            console.print("password updated (not echoed).")
        if has_totp:
            console.print("TOTP is enrolled — you'll still need your authenticator code.")

    asyncio.run(_run())


@app.command()
def login(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    api_url: str | None = typer.Option(None, help="API base URL (default from context)."),
) -> None:
    """Sign in to the API and store the session in ~/.config/content-factory/context.json."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    if api_url:
        ctx.api_url = api_url
    client = ApiClient(ctx)
    try:
        view = client.login(username, password)
        if view.get("mfa_required"):
            code = typer.prompt("One-time code")
            view = client.totp(code)
    except ApiError as exc:
        console.print(f"[red]login failed:[/] {exc.detail}")
        raise typer.Exit(code=1) from exc
    ctx.workspace_id = view.get("current_workspace_id")
    ctx.save()
    console.print(
        f"signed in as [bold]{view['account']['username']}[/]; workspaces: {[w['slug'] for w in view['workspaces']]}"  # noqa: E501
    )


@app.command()
def whoami() -> None:
    """Show the current session and workspace."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    try:
        view = ApiClient(ctx).session()
    except ApiError as exc:
        console.print(f"[yellow]not signed in[/] ({exc.status}); run `content-factory login`")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(view))


@app.command()
def logout() -> None:
    """Revoke the CLI session."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    try:
        ApiClient(ctx).logout()
    except ApiError:
        pass
    ctx.save()
    console.print("signed out")


workspaces_app = typer.Typer(help="Workspaces")
app.add_typer(workspaces_app, name="workspaces")


@workspaces_app.command("list")
def workspaces_list() -> None:
    from content_factory.cli.client import ApiClient
    from content_factory.cli.context import CliContext

    for w in ApiClient(CliContext.load()).workspaces():
        console.print(f"{w['id']}  {w['slug']:<20} {w['name']:<30} {w['role']}")


@workspaces_app.command("use")
def workspaces_use(workspace_id: str) -> None:
    from content_factory.cli.client import ApiClient
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    view = ApiClient(ctx).switch_workspace(workspace_id)
    ctx.workspace_id = view["current_workspace_id"]
    ctx.save()
    console.print(f"using workspace {ctx.workspace_id}")


@app.command()
def worker(
    queue: str = typer.Option("control", help="Task queue / resource class to serve."),
) -> None:
    """Run a Temporal worker for one task queue."""
    from content_factory.workflows.worker import main as run

    run(queue)


@app.command("services")
def services(
    action: str = typer.Argument("status", help="status | stop"),
    tenant: str = typer.Option("", help="hidream | comfyui (stop: default both)"),
) -> None:
    """Local GPU servers the stages start themselves (services/local.py): show who answers, or stop
    them to give the GPU back (the last tenant stays up after a run so a rerun is instant)."""
    from content_factory.services.local import TENANTS, LocalServices, Tenant

    if tenant and tenant not in TENANTS:
        typer.echo(f"tenant must be one of {', '.join(TENANTS)}", err=True)
        raise typer.Exit(code=2)
    if action not in {"status", "stop"}:
        typer.echo("action must be status or stop", err=True)
        raise typer.Exit(code=2)
    tenants: list[Tenant] = [cast("Tenant", tenant)] if tenant else list(TENANTS)
    with LocalServices() as svc:
        if action == "status":
            for t in tenants:
                state = "ready" if svc.ready(t) else ("loading" if svc.responding(t) else "down")
                typer.echo(f"{t:8} {state:8} {svc.endpoints[t]}")
            return
        for t in tenants:
            if svc.responding(t):
                svc.stop(t)
                typer.echo(f"{t} stopped")
            else:
                typer.echo(f"{t} not running")


@app.command("stop")
def stop(
    runs: bool = typer.Option(None, "--runs/--no-runs", help="The workflow: local and durable."),
    apps: bool = typer.Option(None, "--apps/--no-apps", help="Worker, API, web dev server."),
    services_: bool = typer.Option(
        None, "--services/--no-services", help="HiDream, ComfyUI, Ollama models."
    ),
    docker: bool = typer.Option(None, "--docker/--no-docker", help="The compose stack."),
    run: str = typer.Option("", "--run", help="One run only: a local run key or a run id."),
    after_stage: bool = typer.Option(
        False, "--after-stage", help="Let the stage in flight finish; stop at the next boundary."
    ),
    grace: float = typer.Option(10.0, help="Seconds between SIGTERM and SIGKILL."),
    reason: str = typer.Option("stopped from the CLI", help="Recorded on the run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Say what would stop; touch nothing."),
) -> None:
    """Stop everything this repo started: the run, the worker, the servers, the GPU, compose.

    With no flags it stops all four. Name some (`--runs --services`) to stop only those, or
    subtract (`--no-docker`) to keep the rest of the stack up. Nothing outside this checkout is
    ever signalled, and the MCP server is left alone because an assistant is connected through it.
    """
    from content_factory.services import stop as stop_svc

    chosen = {"runs": runs, "apps": apps, "services": services_, "docker": docker}
    if any(v is True for v in chosen.values()):
        targets = [name for name, value in chosen.items() if value is True]
    elif run and all(value is None for value in chosen.values()):
        # Naming one run is asking about that run. Taking compose down as well because no target
        # flag was typed would be the opposite of what `--run` says.
        targets = ["runs"]
    else:
        targets = [name for name, value in chosen.items() if value is not False]
    outcomes = stop_svc.stop(
        targets=targets,
        run=run,
        graceful=after_stage,
        grace_s=grace,
        dry_run=dry_run,
        actor=os.environ.get("USER", "operator"),
        reason=reason,
    )
    for outcome in outcomes:
        typer.echo(str(outcome))


gpu_app = typer.Typer(
    help="Share the card with a higher-priority tenant: park the run, then put it back."
)
app.add_typer(gpu_app, name="gpu")


@gpu_app.command("yield")
def gpu_yield(
    need_gb: float = typer.Option(
        17.5, "--need-gb", help="Free VRAM the other tenant needs, in GiB."
    ),
    reason: str = typer.Option(
        "flashcards", "--reason", help="Who is asking. Recorded on the run."
    ),
    deadline: float = typer.Option(
        90.0, "--deadline", help="Seconds to wait for a clean stage boundary before signalling."
    ),
    keep_services: bool = typer.Option(
        False, "--keep-services", help="Leave HiDream/ComfyUI/Ollama loaded (frees far less)."
    ),
) -> None:
    """Make room on the GPU, parking the local run only if that is what it takes.

    Returns immediately when enough is already free, so this is cheap to call on every session
    start. Exits non-zero when the card still cannot fit the request after everything was stopped,
    which is the caller's signal to degrade rather than OOM.
    """
    from content_factory.services import gpu_priority

    outcome = gpu_priority.yield_gpu(
        need_gib=need_gb, reason=reason, deadline_s=deadline, stop_services=not keep_services
    )
    typer.echo(str(outcome))
    for parked in outcome.parked:
        typer.echo(f"  parked {parked.run_key} at {parked.step or '(not started)'}")
    if not outcome.ok:
        raise typer.Exit(1)


@gpu_app.command("resume")
def gpu_resume(
    print_only: bool = typer.Option(
        False, "--print-only", help="Show the resume command; start nothing and keep the claim."
    ),
    stale_after: float = typer.Option(
        0.0,
        "--stale-after",
        help="Only resume if the claim is older than this many seconds (for a cron safety net).",
    ),
) -> None:
    """Put back whatever `gpu yield` parked, and drop the claim.

    Idempotent: with no claim, or a claim that parked nothing, this says so and does nothing. Safe
    to call from the other tenant's end-of-session hook and from a timer both — the hook passes no
    `--stale-after` because it knows the session ended; the timer passes one longer than a session
    so it cannot take the card back from somebody still using it.
    """
    from content_factory.services import gpu_priority

    outcome = gpu_priority.resume_gpu(print_only=print_only, stale_after_s=stale_after)
    typer.echo(outcome.note)
    for command in outcome.commands:
        typer.echo("  " + " ".join(command))


@gpu_app.command("status")
def gpu_status() -> None:
    """Who holds the card, what is parked, and how much VRAM is free."""
    import time as _time

    from content_factory.services import gpu_priority

    free = gpu_priority.FREE_VRAM_GIB()
    typer.echo(f"free VRAM: {'unknown' if free is None else f'{free:.1f} GiB'}")
    claim = gpu_priority.read_claim()
    if claim is None:
        typer.echo("no claim: the GPU is this repo's to use")
        return
    held = int(claim.held_for(_time.time()))
    typer.echo(
        f"claimed by {claim.reason!r} for {held // 60}m{held % 60:02d}s, "
        f"needs {claim.need_gib:.1f} GiB"
    )
    for parked in claim.parked:
        typer.echo(f"  parked {parked.run_key} at {parked.step or '(not started)'}")
        typer.echo("    " + " ".join(parked.resume_command()))


assets_app = typer.Typer(help="Character-asset review: check a built sculpture, then approve it.")
app.add_typer(assets_app, name="assets")
frames_app = typer.Typer(help="Frame review: look at the contact sheet, then record a verdict.")
app.add_typer(frames_app, name="frames")


@assets_app.command("review")
def assets_review(
    asset: str = typer.Argument("", help="Asset name; omit to review every built asset"),
    sheet_dir: str = typer.Option("output/asset-reviews", help="Where contact sheets are written"),
) -> None:
    """Run the deterministic checks over a built asset and write its contact sheet to look at."""
    import pathlib

    from content_factory.config import get_settings
    from content_factory.controls.asset_review import review_asset

    root = pathlib.Path(get_settings().controls.assets_root) / "characters"
    names = [asset] if asset else sorted(d.name for d in root.iterdir() if d.is_dir())
    out = pathlib.Path(sheet_dir)
    for name in names:
        review = review_asset(root / name, sheet_dest=out / f"{name}.sheet.png")
        (out / f"{name}.review.json").write_text(review.model_dump_json(indent=1))
        typer.echo(f"{name}: checks {'pass' if review.checks_passed else 'FAIL'}")
        for check in review.checks:
            mark = (
                "  ok " if check.passed else ("  !! " if check.severity == "blocker" else "  ~~ ")
            )
            typer.echo(f"{mark}{check.check}: {check.detail}")
        typer.echo(f"  look at {out / f'{name}.sheet.png'}")


@assets_app.command("approve")
def assets_approve(
    asset: str = typer.Argument(..., help="Asset name"),
    reviewer: str = typer.Option(..., "--as", help="Who looked at it"),
    note: str = typer.Option("", help="What you saw"),
    approvals_dir: str = typer.Option(
        "", help="Defaults to controls.asset_approvals_dir, which is where review_assets looks"
    ),
) -> None:
    """Approve a built asset for use. The approval binds to the mesh digest: rebuild it and this
    approval no longer applies."""
    import datetime as dt
    import pathlib

    from content_factory.config import get_settings
    from content_factory.controls.asset_review import review_asset

    root = pathlib.Path(get_settings().controls.assets_root) / "characters"
    review = review_asset(root / asset)
    if not review.checks_passed:
        typer.echo(f"{asset} fails its checks; fix the asset rather than approving it:", err=True)
        for check in review.blockers:
            typer.echo(f"  !! {check.check}: {check.detail}", err=True)
        raise typer.Exit(code=1)
    approved = review.model_copy(
        update={
            "approved_by": reviewer,
            "approved_at": dt.datetime.now(dt.UTC),
            "notes": note or review.notes,
        }
    )
    out = pathlib.Path(approvals_dir or get_settings().controls.asset_approvals_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{asset}.json").write_text(approved.model_dump_json(indent=1))
    typer.echo(
        json.dumps({"asset": asset, "blend": approved.blend_sha256[:12], "approved_by": reviewer})
    )


@frames_app.command("review")
def frames_review(
    project_dir: str = typer.Argument(..., help="The run's project directory"),
    deliverable: str = typer.Option("dlv_short0000001"),
    accept_all: bool = typer.Option(False, "--accept-all", help="Accept every frame"),
    reject: str = typer.Option("", help="Comma-separated frame ids to reject"),
    reason: str = typer.Option("", help="Why those frames were rejected"),
    reviewer: str = typer.Option("operator", "--as"),
) -> None:
    """Record a verdict on a batch of frames. Run the pipeline's review_frames stage first: it
    writes the contact sheet and the batch this command decides on."""
    import datetime as dt
    import pathlib

    from content_factory.schemas.review import FrameReviewBatch

    base = pathlib.Path(project_dir) / "deliverables" / deliverable / "reviews" / "frames"
    batch_path = base / "batch.json"
    if not batch_path.exists():
        typer.echo(f"no batch at {batch_path}; run the review_frames stage first", err=True)
        raise typer.Exit(code=1)
    batch = FrameReviewBatch.model_validate_json(batch_path.read_text())
    rejected = {f.strip() for f in reject.split(",") if f.strip()}
    unknown = rejected - {f.frame_id for f in batch.frames}
    if unknown:
        typer.echo(f"unknown frame id(s): {sorted(unknown)}", err=True)
        raise typer.Exit(code=1)
    if not accept_all and not rejected:
        typer.echo(f"look at {base / 'contact-sheet.png'} — {len(batch.frames)} frames", err=True)
        typer.echo("then pass --accept-all, or --reject <ids> --reason <why>", err=True)
        raise typer.Exit(code=2)
    decided = batch.model_copy(
        update={
            "reviewer": reviewer,  # type: ignore[arg-type]
            "reviewed_at": dt.datetime.now(dt.UTC),
            "frames": tuple(
                f.model_copy(
                    update={
                        "verdict": "reject" if f.frame_id in rejected else "accept",
                        "reason": reason if f.frame_id in rejected else "",
                    }
                )
                for f in batch.frames
            ),
        }
    )
    (base / "verdict.json").write_text(decided.model_dump_json(indent=1))
    typer.echo(
        json.dumps(
            {
                "frames": len(decided.frames),
                "accepted": sum(1 for f in decided.frames if f.verdict == "accept"),
                "rejected": [f.frame_id for f in decided.rejected],
                "passed": decided.passed,
            }
        )
    )


prompting_app = typer.Typer(
    help="Prompt guidance that learns from review verdicts. Proposals only: nothing edits the "
    "guidance without a named person who has seen the diff."
)
app.add_typer(prompting_app, name="prompting")


@prompting_app.command("propose")
def prompting_propose(
    project_dir: str = typer.Argument(..., help="A run whose frame reviews hold the evidence"),
    out_dir: str = typer.Option("output/prompting-proposals"),
) -> None:
    """Read the frame-review verdicts and write a proposal. Never touches the guidance file."""
    import pathlib

    from content_factory.prompting import lessons_from_review, load_batches, write_proposal

    batches = load_batches(pathlib.Path(project_dir))
    if not batches:
        typer.echo(f"no frame-review batches under {project_dir}", err=True)
        raise typer.Exit(code=1)
    lessons = lessons_from_review(batches)
    if not lessons:
        typer.echo(
            json.dumps({"batches": len(batches), "lessons": 0, "note": "nothing recurring to add"})
        )
        return
    proposal = write_proposal(lessons, pathlib.Path(out_dir))
    typer.echo(f"proposal {proposal.proposal_id} — from {len(batches)} review batch(es)\n")
    for lesson in proposal.lessons:
        typer.echo(f"  {lesson.title}")
        typer.echo(f"    {lesson.guidance}")
        typer.echo(f"    seen {lesson.occurrences}x in {len(lesson.frame_ids)} frame(s)\n")
    typer.echo("This is what would change:\n")
    typer.echo(proposal.diff)
    typer.echo(
        f"\nNothing has been changed. To accept it:\n"
        f"  content-factory prompting apply {proposal.proposal_id} --as <your name>"
    )


@prompting_app.command("apply")
def prompting_apply(
    proposal_id: str = typer.Argument(..., help="From `prompting propose`"),
    reviewer: str = typer.Option(..., "--as", help="Who read the diff and accepts it"),
    out_dir: str = typer.Option("output/prompting-proposals"),
) -> None:
    """Apply a proposal you have read. Refuses if the guidance changed since it was written."""
    import pathlib

    from content_factory.prompting import ProposalRefusedError, apply_proposal, load_proposal

    path = pathlib.Path(out_dir) / f"{proposal_id}.json"
    if not path.exists():
        typer.echo(f"no proposal {proposal_id} in {out_dir}", err=True)
        raise typer.Exit(code=1)
    proposal = load_proposal(path)
    try:
        applied = apply_proposal(proposal, applied_by=reviewer)
    except ProposalRefusedError as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path.write_text(applied.model_dump_json(indent=1))
    typer.echo(
        json.dumps(
            {
                "proposal": applied.proposal_id,
                "applied_by": reviewer,
                "lessons": len(applied.lessons),
                "skill": applied.skill_path,
            }
        )
    )


@app.command("run-local")
def run_local(
    workflow: str = typer.Argument(
        "picture-story",
        help="A workflow id. Run `content-factory workflows list` for the catalogue.",
    ),
    project_dir: str = typer.Option(
        "", help="Where the run writes (default output/local-runs/<workflow>)"
    ),
    from_stage: str = typer.Option(
        "", "--from", help="Resume at this stage (earlier outputs kept)"
    ),
    until_stage: str = typer.Option("", "--until", help="Stop after this stage"),
    quality: str = typer.Option("demo"),
    story: str = typer.Option(
        "", "--story", help="StoryPlan fixture (repo-relative), e.g. fixtures/story/love_story.json"
    ),
    shots: str = typer.Option(
        "", "--shots", help="ShotPlan fixture (repo-relative); implies planner=fixture"
    ),
    subject: str = typer.Option(
        "",
        "--subject",
        help="One sentence naming the film's world; leads the anchor prompt "
        "(make_story_fixtures.py prints each scenario's)",
    ),
    style: str = typer.Option(
        "",
        "--style",
        help="Art direction: a preset name (content_factory.sequences.styles) or a full prompt",
    ),
) -> None:
    """Run one workflow's stages locally in order (no Temporal). Stages that need the HiDream
    server or ComfyUI start them themselves (local_services.auto_start) and hand the GPU over
    between them. Backends come from the environment: CF__CONTROLS__COMPILER=blender
    CF__IMAGE_SEQUENCES__BACKEND=hidream CF__VIDEO__BACKEND=comfyui CF__NARRATION__TTS=qwen3tts."""
    from pathlib import Path

    from content_factory.runners.local import LocalRunError, run_workflow
    from content_factory.runners.registry import RunStopped

    try:
        report = run_workflow(
            workflow,
            project_dir=Path(project_dir).expanduser() if project_dir else None,
            from_stage=from_stage or None,
            until_stage=until_stage or None,
            quality=quality,
            story=story or None,
            shots=shots or None,
            style=style or None,
            subject=subject or None,
            log=typer.echo,
        )
    except RunStopped as exc:
        typer.echo(f"stopped at {exc.at}: {exc.reason}", err=True)
        if exc.at:
            typer.echo(f"resume with: --from {exc.at}", err=True)
        raise typer.Exit(code=130) from exc
    except LocalRunError as exc:
        typer.echo(f"run failed at {exc.stage.value}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"passed": report["passed"], "stages": len(report["stages"])}))


@app.command()
def demo(quality: str = typer.Option("smoke", help="smoke (one scene) | demo (full clip)")) -> None:
    """Offline demo: fixture campaign → DAG → single-image PNG + short MP4 → QC → run report."""
    from content_factory.runners.demo import run_demo

    if quality not in {"smoke", "demo"}:
        raise typer.BadParameter("quality must be smoke or demo")
    report = run_demo(quality=quality)
    for did, d in report["deliverables"].items():
        status = "[green]PASS[/]" if d["qc_passed"] else "[red]FAIL[/]"
        console.print(f"{status} {d['type']:<18} {did}  {d['artifact']['key']}")
        for f in d["qc"]:
            console.print(f"     - {f['severity']}: {f['check']}: {f['message']}")
    console.print(
        f"run report: projects/{report['project_id']}/final/run-report.json ({report['elapsed_s']} s)"  # noqa: E501
    )
    raise typer.Exit(code=0 if report["passed"] else 1)


from content_factory.cli import reference_cmd, workflows_cmd  # noqa: E402

app.add_typer(workflows_cmd.app, name="workflows")
app.add_typer(reference_cmd.app, name="reference")
# `make` is the one command an agent needs: one call runs a whole production.
app.command("make")(workflows_cmd.make)

runs_app = typer.Typer(help="Production runs")
app.add_typer(runs_app, name="runs")


@runs_app.command("start")
def runs_start(quality: str = typer.Option("demo")) -> None:
    """Start the fixture campaign as a durable production run (requires worker + temporal)."""
    import asyncio

    from content_factory.schemas.fixtures import sample_campaign
    from content_factory.services.runs import start_run

    run_id = asyncio.run(start_run(sample_campaign(), quality=quality))
    console.print(
        f"started [bold]{run_id}[/] — approve with: content-factory runs approve {run_id}"
    )


@runs_app.command("status")
def runs_status(run_id: str) -> None:
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS
    from content_factory.services.runs import run_view

    async def _run() -> None:
        async with session_scope() as db:
            view = await run_view(db, WS, run_id)
        if view is None:
            console.print("[red]not found[/]")
            raise typer.Exit(1)
        console.print(f"[bold]{view['state']}[/] {view['run_id']}  project={view['project_id']}")
        for n in view["nodes"]:
            mark = {
                "complete": "[green]✔[/]",
                "running": "[yellow]…[/]",
                "failed": "[red]✘[/]",
                "queued": "·",
            }.get(n["state"], n["state"])
            cache = " (cache)" if n["cache_hit"] else ""
            console.print(f"  {mark} {n['node_id']}{cache}")

    asyncio.run(_run())


@runs_app.command("approve")
def runs_approve(
    run_id: str, reject: bool = typer.Option(False), reason: str = typer.Option("")
) -> None:
    """Approve (or reject) the waiting preflight revision of a run."""
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS
    from content_factory.services.runs import approve_run, run_view

    async def _run() -> None:
        async with session_scope() as db:
            view = await run_view(db, WS, run_id)
        if view is None or not view["preflight_revision_hash"]:
            console.print("[red]run not found or not waiting for approval[/]")
            raise typer.Exit(1)
        await approve_run(
            run_id,
            actor="cli-operator",
            revision_hash=view["preflight_revision_hash"],
            decision="reject" if reject else "approve",
            reason=reason,
        )
        console.print(
            "rejected" if reject else f"approved revision {view['preflight_revision_hash'][:12]}"
        )

    asyncio.run(_run())


@runs_app.command("stop")
def runs_stop(
    run_id: str = typer.Argument("", help="Run id; omit to stop every run Temporal has open."),
    reason: str = typer.Option("", help="Recorded on the run."),
    after_node: bool = typer.Option(
        False, "--after-node", help="Stop at the next node boundary; do not cancel the node."
    ),
) -> None:
    """Stop a durable run. The workflow closes itself (CANCELLED, ActionItems resolved); a node
    already inside a GPU stage is cancelled after 15 s and the run row is corrected from here."""
    import asyncio

    from content_factory.services.runs import open_run_ids, stop_run

    async def _run() -> None:
        run_ids = [run_id] if run_id else await open_run_ids()
        if not run_ids:
            console.print("no run is executing")
            return
        # Together, not one after another: each stop waits for its run to close itself before
        # cancelling it, and a dev Temporal can hold dozens of runs whose worker is long gone.
        results = await asyncio.gather(
            *(
                stop_run(
                    rid,
                    actor=os.environ.get("USER", "cli-operator"),
                    reason=reason,
                    graceful=after_node,
                )
                for rid in run_ids
            )
        )
        for rid, result in zip(run_ids, results, strict=True):
            console.print(f"{rid} [bold]{result['outcome']}[/]")

    asyncio.run(_run())


@runs_app.command("start-documentary")
def runs_start_documentary(
    topic: str = typer.Option(..., help="Episode topic or high-level video instruction"),
    objective: str = typer.Option("", help="What the episode should make the viewer understand"),
    minutes: int = typer.Option(10, help="Target long-form duration in minutes (1-60)"),
    shorts: int = typer.Option(3, help="Vertical shorts derived as excerpts (0-10)"),
    quality: str = typer.Option("demo"),
    dry_run: bool = typer.Option(False, help="Compile and print the plan without starting a run"),
) -> None:
    """Documentary episode: one 16:9 long form plus independently re-edited 9:16 shorts."""
    import asyncio

    from content_factory.deliverables.dag_compiler import compile_dag
    from content_factory.deliverables.documentary import documentary_campaign
    from content_factory.schemas.fixtures import WS

    if not 1 <= minutes <= 60 or not 0 <= shorts <= 10:
        console.print("[red]minutes must be 1-60 and shorts 0-10[/]")
        raise typer.Exit(2)
    campaign = documentary_campaign(
        workspace_id=WS, topic=topic, objective=objective, minutes=minutes, shorts=shorts
    )
    dag = compile_dag(campaign)
    console.print(
        f"campaign [bold]{campaign.campaign_id}[/]: {len(campaign.deliverables)} deliverables, "
        f"{len(dag.nodes)} stage nodes, {len(dag.pruned)} pruned"
    )
    if dry_run:
        for d in campaign.deliverables:
            console.print(f"  {d.type:12} {d.deliverable_id}  {d.title}")
        return
    from content_factory.services.runs import start_run

    run_id = asyncio.run(start_run(campaign, quality=quality))
    console.print(
        f"started [bold]{run_id}[/] — approve with: content-factory runs approve {run_id}"
    )


@app.command("node-report")
def node_report(
    node_id: str = typer.Option("nde_localprobe1", help="Stable node id for this machine"),
    skip_encoders: bool = typer.Option(False, help="Skip the real ffmpeg test encodes"),
) -> None:
    """Probe THIS machine into a NodeCapabilityReport (hardware, volumes, verified encoders)."""
    from content_factory.hardware.capability import build_capability_report

    report = build_capability_report(node_id, encoders=() if skip_encoders else None)
    console.print_json(report.model_dump_json())


@app.command("video-stack")
def video_stack(
    root: str = typer.Option(
        "",
        help="Stack root (default: $AI_VIDEO_ROOT or the repo root; checkouts in <root>/external)",
    ),
    models: str = typer.Option(
        "",
        help=(
            "Weight store (default: $AI_VIDEO_MODELS, else <root>/models if it exists, else the"
            " first existing CF__COMFYUI__EXTRA_MODEL_ROOTS entry)"
        ),
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
) -> None:
    """Verify the local AI-video model stack on disk: what is usable, gated, or missing."""
    import json as _json
    import os
    from dataclasses import asdict
    from pathlib import Path

    from content_factory.config import get_settings
    from content_factory.models.video_stack import (
        StackStatus,
        default_stack_root,
        resolve_models_root,
        verify_video_stack,
    )

    stack_root = Path(root).expanduser() if root else default_stack_root()
    models_root = resolve_models_root(
        stack_root,
        explicit=models or os.environ.get("AI_VIDEO_MODELS", ""),
        configured_roots=get_settings().comfyui.extra_model_roots,
    )
    report = verify_video_stack(stack_root, models_root=models_root)
    if as_json:
        console.print_json(_json.dumps([asdict(e) for e in report.entries], default=str))
        return
    mark = {
        StackStatus.ready: "[green]READY[/]",
        StackStatus.downloading: "[yellow]DOWNLOADING[/]",
        StackStatus.gated_pending: "[yellow]GATED[/]",
        StackStatus.missing_weights: "[red]NO WEIGHTS[/]",
        StackStatus.not_downloaded: "[red]NOT DOWNLOADED[/]",
        StackStatus.manual_step: "[yellow]MANUAL STEP[/]",
        StackStatus.incompatible: "[red]INCOMPATIBLE[/]",
        StackStatus.wont_fit_24gb: "[red]WON'T FIT 24GB[/]",
        StackStatus.tool_missing: "[red]TOOL MISSING[/]",
    }
    console.print(
        f"[bold]AI video stack[/] at {stack_root}  [dim](weights: {report.models_root})[/]"
    )
    tier = None
    for e in report.entries:
        if e.entry.tier != tier:
            tier = e.entry.tier
            console.print(f"\n[bold]{tier.value.upper().replace('_', ' ')}[/]")
        console.print(f"  {mark[e.status]:24} {e.entry.name}  [dim]({e.entry.role})[/]")
        if e.status != StackStatus.ready and (e.entry.caveat or e.detail):
            console.print(f"      [dim]{e.entry.caveat or e.detail}[/]")
        if e.entry.recommendation:
            console.print(f"      [cyan]→ {e.entry.recommendation}[/]")
    console.print(f"\nsummary: {report.summary()}")


distribution_app = typer.Typer(help="Distribution safety controls")
app.add_typer(distribution_app, name="distribution")


@distribution_app.command("kill-switch")
def distribution_kill_switch(
    state: str = typer.Argument(..., help="on | off"),
    confirm: bool = typer.Option(False, "--confirm", help="Required."),
) -> None:
    """Flip the global distribution kill switch for the fixture workspace (also in the UI/PWA)."""
    if state not in {"on", "off"}:
        raise typer.BadParameter("state must be on or off")
    if not confirm:
        console.print("[red]refusing without --confirm[/]")
        raise typer.Exit(1)
    import asyncio

    from content_factory.db.base import utcnow
    from content_factory.db.models import DistributionState
    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS

    async def _run() -> None:
        async with session_scope() as db:
            row = await db.get(DistributionState, WS)
            if row is None:
                row = DistributionState(workspace_id=WS, kill_switch=state == "on")
                db.add(row)
            else:
                row.kill_switch = state == "on"
            row.updated_by = "cli-operator"
            row.updated_at = utcnow()
        console.print(f"kill switch is now [bold]{state.upper()}[/]")

    asyncio.run(_run())


audit_app = typer.Typer(help="Audit log")
app.add_typer(audit_app, name="audit")


@audit_app.command("export")
def audit_export(
    since: str = typer.Option("1970-01-01", help="ISO date lower bound."),
    out: str = typer.Option("-", help="Output file (JSONL) or - for stdout."),
) -> None:
    """SIEM-shaped JSONL export of the append-only audit log."""
    import asyncio
    import json as _json
    import sys
    from datetime import date

    from sqlalchemy import select

    from content_factory.db.models import AuditEvent
    from content_factory.db.session import session_scope

    lower = date.fromisoformat(since)

    async def _run() -> None:
        async with session_scope() as db:
            rows = (
                (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at)))
                .scalars()
                .all()
            )
        stream = sys.stdout if out == "-" else open(out, "w", encoding="utf-8")  # noqa: ASYNC230
        try:
            n = 0
            for a in rows:
                if a.created_at.date() < lower:
                    continue
                stream.write(
                    _json.dumps(
                        {
                            "ts": a.created_at.isoformat(),
                            "event.id": a.id,
                            "event.action": a.action,
                            "actor.id": a.actor_account_id,
                            "workspace.id": a.workspace_id,
                            "target.type": a.target_type,
                            "target.id": a.target_id,
                            "source.ip": a.ip,
                            "event.detail": a.detail,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                n += 1
        finally:
            if stream is not sys.stdout:
                stream.close()
        if out != "-":
            console.print(f"wrote {n} events to {out}")

    asyncio.run(_run())


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio (the only external-agent surface)."""
    from content_factory.mcp_server import main as run_mcp

    run_mcp()


@app.command()
def config(
    show_defaults: bool = typer.Option(False, help="Print the effective configuration as YAML."),
) -> None:
    """Validate and print the effective configuration (secrets are never printed)."""
    from content_factory.config import get_settings

    settings = get_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), indent=2))
    _ = show_defaults
