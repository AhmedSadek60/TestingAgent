# Architecture

The current architecture of AgentLab is described in [architecture.md](../architecture.md): the parts and what each is
for, the run phase by phase, how a target is reached, how a result is judged and where AgentLab stops.

* [Security model](../security.md): what AgentLab trusts, what it refuses, and the tests that show it.
* [Plug-ins](../plugins.md): the registries, what a plug-in is trusted with, and what is not pluggable.
* [Running it](../installation.md), [in Docker](../installation.md#docker) and [on Railway](../deployment-railway.md).
* [Decisions](../decisions/README.md): the *why*, as architecture decision records.

Rules: keep it accurate and concise; link to ADRs in `../decisions/` for the *why*; mark anything unverified as
"unknown" rather than guessing. What was verified, and what was not, is in
[development.md](../development.md#verification-status).
