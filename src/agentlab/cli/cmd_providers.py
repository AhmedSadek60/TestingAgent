"""``agentlab providers`` and ``agentlab models``: which LLM providers are configured and what they offer.

Provider keys are *references* (``env:NAME`` or ``secret:NAME``). These commands show whether a reference resolves, never
the value. Commands that contact a provider say so and only run when asked.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import EXIT_INCOMPLETE, console, emit_json, make_services, run_async, state
from agentlab.cli.markup import esc
from agentlab.core.errors import UserError
from agentlab.diagnostics import check_provider, key_status, provider_works
from agentlab.security.redactor import redact

providers_app = typer.Typer(help="Configured LLM providers.", no_args_is_help=True)
models_app = typer.Typer(help="Models the configured providers offer.", no_args_is_help=True)


@providers_app.command("list")
def providers_list(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List configured providers: type, endpoint, default model, declared capabilities and whether the key is set.

    Nothing is sent to any provider.
    """
    services = make_services(state(ctx), migrate=False)
    rows = services.providers.describe()
    for r in rows:
        ref = r.get("api_key_ref")
        r["key"] = key_status(services, ref if isinstance(ref, str) else None)
    judges = {j.provider for j in services.config.evaluation.judges}
    run_async(services.aclose())
    if as_json:
        emit_json([{**r, "judge": r["name"] in judges} for r in rows])
        return
    t = Table("Name", "Type", "Endpoint", "Model", "Capabilities", "Key", "Judge", box=box.SIMPLE_HEAD)
    for r in rows:
        caps = ", ".join(r["capabilities"]) if isinstance(r["capabilities"], list) else ""
        key = str(r["key"])
        t.add_row(
            esc(r["name"]),
            esc(r["type"]),
            esc(r["base_url"] or "default"),
            esc(r["model"] or "-"),
            esc(caps) or f"[red]{esc(r['configured_error'])}[/red]",
            f"[red]{esc(key)}[/red]" if key.startswith(("MISSING", "invalid")) else esc(key),
            "yes" if r["name"] in judges else "",
        )
    console.print(t)
    console.print(
        "[dim]Capabilities are what each adapter declares or the config overrides; `agentlab providers check` verifies them live.[/dim]"
    )


@providers_app.command("check")
def providers_check(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Provider name from agentlab.yaml.")],
    model: Annotated[str | None, typer.Option("--model", help="Model to ask (default: the configured one).")] = None,
    no_complete: Annotated[
        bool, typer.Option("--no-complete", help="Only list models; do not send a completion request.")
    ] = False,
) -> None:
    """Contact the provider (a model listing and one tiny completion) to verify the endpoint, key and model.

    This sends a request with the provider's credentials, which may be billed; it only runs when you ask for it.
    """
    services = make_services(state(ctx), migrate=False)
    services.config.provider(name)  # unknown name -> exit 2

    async def go() -> dict[str, Any]:
        try:
            return await check_provider(services, name, model=model, complete=not no_complete)
        finally:
            await services.aclose()

    res = run_async(go())
    ok = provider_works(res)
    for k, v in res.items():
        if v is not None:
            console.print(f"[bold]{esc(k)}[/bold] {esc(v)}")
    console.print("[green]provider works[/green]" if ok else "[red]provider check failed[/red]")
    if not ok:
        raise typer.Exit(EXIT_INCOMPLETE)


@models_app.command("list")
def models_list(
    ctx: typer.Context,
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Only this provider.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the models each configured provider reports (asks the provider's model-listing endpoint)."""
    services = make_services(state(ctx), migrate=False)
    names = [provider] if provider else services.providers.names()
    if provider and provider not in services.providers.names():
        raise UserError(f"provider '{provider}' is not configured (known: {', '.join(services.providers.names())})")

    async def go() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            for n in names:
                try:
                    models = await services.providers.discover_models(n)
                    rows += [
                        {
                            "provider": n,
                            "model": m.id,
                            "context": m.context_length,
                            "capabilities": [c.value for c in m.capabilities],
                            "error": None,
                        }
                        for m in models
                    ] or [
                        {
                            "provider": n,
                            "model": None,
                            "context": None,
                            "capabilities": [],
                            "error": "no models reported",
                        }
                    ]
                except Exception as exc:
                    rows.append(
                        {
                            "provider": n,
                            "model": None,
                            "context": None,
                            "capabilities": [],
                            "error": redact(f"{type(exc).__name__}: {exc}")[:160],
                        }
                    )
        finally:
            await services.aclose()
        return rows

    rows = run_async(go())
    if as_json:
        emit_json(rows)
        return
    t = Table("Provider", "Model", "Context", "Capabilities", box=box.SIMPLE_HEAD)
    for r in rows:
        if r["error"]:
            t.add_row(esc(r["provider"]), f"[dim]{esc(r['error'])}[/dim]", "", "")
        else:
            t.add_row(esc(r["provider"]), esc(r["model"]), esc(r["context"] or ""), esc(", ".join(r["capabilities"])))
    console.print(t)
