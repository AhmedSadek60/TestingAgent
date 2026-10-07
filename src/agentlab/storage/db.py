"""Database engine and the :class:`Store` facade used by the orchestrator, API and CLI."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agentlab.core.errors import NotFoundError, UserError
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models import AgentProfile, Finding, Scorecard, TargetSpec, TestCase, TestResult
from agentlab.security.credentials import CredentialProfile
from agentlab.storage import orm
from agentlab.storage.artifacts import ArtifactRef
from agentlab.tracing.events import Event
from agentlab.tracing.trace import Trace


def row_to_dict(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        v = getattr(row, col.key)
        out[col.key] = v.isoformat() if isinstance(v, datetime) else v
    return out


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def normalise_database_url(url: str) -> str:
    """The URL a hosting platform hands out, in the form SQLAlchemy needs for the driver AgentLab installs.

    Railway, Heroku, Render and others give a PostgreSQL URL as ``postgres://`` or ``postgresql://``. SQLAlchemy opens
    those with psycopg2, which AgentLab does not install (it uses psycopg 3), so both mean ``postgresql+psycopg://``.
    A URL that names its driver, and every other database, is returned as it is."""
    for scheme in ("postgres://", "postgresql://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


def dump_json(value: Any) -> str:
    """JSON with its characters as they are and not as ``\\uXXXX`` escapes, which a SQL_ASCII database cannot keep in a
    JSONB column ("Unicode escape value could not be translated to the server's encoding")."""
    return json.dumps(value, ensure_ascii=False)


def postgres_connect_args(url: str) -> dict[str, Any]:
    """Text goes to and from PostgreSQL as UTF-8 whatever encoding the database was created with.

    A database created as SQL_ASCII (what ``initdb`` makes when the machine's locale is ``C``) otherwise hands text back
    as bytes, which fails at the first query, and cannot store a name in another script at all."""
    return {"client_encoding": "utf8"} if url.startswith("postgresql") else {}


class Database:
    def __init__(self, url: str = "sqlite:///.agentlab/agentlab.db", *, echo: bool = False) -> None:
        url = normalise_database_url(url)
        self.url = url
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if url.startswith("postgresql"):
            kwargs["connect_args"] = postgres_connect_args(url)
            kwargs["json_serializer"] = dump_json
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url == "sqlite://":
                kwargs["poolclass"] = StaticPool
            else:
                path = url.split("///", 1)[-1]
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _):  # type: ignore[no-untyped-def]
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False, future=True)
        self._lock = threading.RLock()

    def create_all(self) -> None:
        orm.Base.metadata.create_all(self.engine)

    def migrate(self) -> None:
        """Bring the schema to the current version with the shipped Alembic migrations."""
        from agentlab.storage.migrate import upgrade

        tables = set(inspect(self.engine).get_table_names())
        if tables and "alembic_version" not in tables:
            raise UserError(
                f"the database {self.url.split('///')[-1] or self.url} has tables but no migration history, so it was "
                "not created by AgentLab. Point storage.database_url at a new file."
            )
        upgrade(self.url)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock:
            s = self._sessions()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

    def dispose(self) -> None:
        self.engine.dispose()


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----------------------------------------------------------------- projects / targets
    def create_project(
        self, name: str, description: str = "", objective: str = "", settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.db.session() as s:
            if s.scalar(select(orm.Project).where(orm.Project.name == name)):
                raise UserError(f"project '{name}' already exists")
            row = orm.Project(name=name, description=description, objective=objective, settings=settings or {})
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def get_project(self, ident: str) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(orm.Project, ident) or s.scalar(select(orm.Project).where(orm.Project.name == ident))
            if row is None:
                raise NotFoundError(f"project '{ident}' not found")
            return row_to_dict(row)

    def ensure_project(self, name: str, **kw: Any) -> dict[str, Any]:
        try:
            return self.get_project(name)
        except UserError:
            return self.create_project(name, **kw)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [row_to_dict(r) for r in s.scalars(select(orm.Project).order_by(orm.Project.created_at))]

    def add_target(self, project_id: str, spec: TargetSpec) -> dict[str, Any]:
        kind = (spec.interfaces() or ["unknown"])[0]
        with self.db.session() as s:
            existing = s.scalar(
                select(orm.Target).where(orm.Target.project_id == project_id, orm.Target.name == spec.name)
            )
            data = spec.model_dump(mode="json")
            if existing:
                existing.spec, existing.kind, existing.target_version = data, kind, spec.version
                existing.version += 1
                s.flush()
                return row_to_dict(existing)
            row = orm.Target(project_id=project_id, name=spec.name, kind=kind, target_version=spec.version, spec=data)
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def get_target(self, target_id: str) -> tuple[dict[str, Any], TargetSpec]:
        with self.db.session() as s:
            row = s.get(orm.Target, target_id)
            if row is None:
                raise NotFoundError(f"target '{target_id}' not found")
            return row_to_dict(row), TargetSpec.model_validate(row.spec)

    def list_targets(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(orm.Target).order_by(orm.Target.created_at)
            if project_id:
                q = q.where(orm.Target.project_id == project_id)
            return [row_to_dict(r) for r in s.scalars(q)]

    def save_repository(
        self,
        target_id: str,
        *,
        url: str | None,
        ref: str | None,
        commit: str | None,
        analysis: dict[str, Any],
        snapshot_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        with self.db.session() as s:
            row = orm.Repository(
                target_id=target_id,
                url=url,
                ref=ref,
                commit=commit,
                analysis=analysis,
                snapshot_artifact_id=snapshot_artifact_id,
            )
            s.add(row)
            s.flush()
            return row_to_dict(row)

    # ----------------------------------------------------------------- credentials (metadata only)
    def upsert_credential_meta(self, profile: CredentialProfile, project_id: str | None = None) -> None:
        with self.db.session() as s:
            row = s.scalar(select(orm.CredentialProfileRow).where(orm.CredentialProfileRow.name == profile.name))
            if row is None:
                row = orm.CredentialProfileRow(name=profile.name, kind=profile.kind, project_id=project_id)
                s.add(row)
            row.kind, row.scopes, row.test_only = profile.kind, profile.scopes, profile.test_only
            row.expires_at, row.secret_version, row.description = (
                profile.expires_at,
                profile.secret_version,
                profile.description,
            )

    def list_credential_meta(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [row_to_dict(r) for r in s.scalars(select(orm.CredentialProfileRow))]

    # ----------------------------------------------------------------- documents
    def add_document(
        self,
        project_id: str,
        name: str,
        media_type: str,
        sha256: str,
        size: int,
        artifact_id: str | None,
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        with self.db.session() as s:
            doc = s.scalar(select(orm.Document).where(orm.Document.project_id == project_id, orm.Document.name == name))
            if doc is None:
                doc = orm.Document(project_id=project_id, name=name, media_type=media_type)
                s.add(doc)
                s.flush()
            last = s.scalar(
                select(orm.DocumentVersion)
                .where(orm.DocumentVersion.document_id == doc.id)
                .order_by(orm.DocumentVersion.version.desc())
            )
            if last is not None and last.sha256 == sha256:
                return {"document_id": doc.id, "version_id": last.id, "version": last.version, "new_version": False}
            ver = orm.DocumentVersion(
                document_id=doc.id,
                sha256=sha256,
                size=size,
                artifact_id=artifact_id,
                parsed=parsed,
                version=(last.version + 1) if last else 1,
            )
            s.add(ver)
            s.flush()
            doc.current_version_id = ver.id
            return {"document_id": doc.id, "version_id": ver.id, "version": ver.version, "new_version": True}

    def list_documents(self, project_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            out = []
            for d in s.scalars(select(orm.Document).where(orm.Document.project_id == project_id)):
                vers = [row_to_dict(v) for v in d.versions]
                out.append({**row_to_dict(d), "versions": vers})
            return out

    # ----------------------------------------------------------------- skills / providers
    def upsert_skill(self, name: str, version: str, origin: str, manifest: dict[str, Any], path: str | None) -> None:
        with self.db.session() as s:
            row = s.scalar(select(orm.SkillRow).where(orm.SkillRow.name == name, orm.SkillRow.skill_version == version))
            if row is None:
                s.add(orm.SkillRow(name=name, skill_version=version, origin=origin, manifest=manifest, path=path))
            else:
                row.manifest, row.origin, row.path = manifest, origin, path

    def list_skills(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [row_to_dict(r) for r in s.scalars(select(orm.SkillRow).order_by(orm.SkillRow.name))]

    def upsert_provider(self, name: str, type_: str, base_url: str | None, config: dict[str, Any]) -> None:
        with self.db.session() as s:
            row = s.scalar(select(orm.ModelProviderRow).where(orm.ModelProviderRow.name == name))
            if row is None:
                s.add(orm.ModelProviderRow(name=name, type=type_, base_url=base_url, config=config))
            else:
                row.type, row.base_url, row.config = type_, base_url, config

    # ----------------------------------------------------------------- profiles
    def save_profile(self, target_id: str, run_id: str | None, profile: AgentProfile) -> dict[str, Any]:
        data = profile.model_dump(mode="json")
        with self.db.session() as s:
            row = orm.AgentProfileRow(
                target_id=target_id,
                run_id=run_id,
                profile=data,
                fingerprint=canonical_hash({"types": data["types"], "tools": data["tools"]}),
            )
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def latest_profile(self, target_id: str) -> AgentProfile | None:
        with self.db.session() as s:
            row = s.scalar(
                select(orm.AgentProfileRow)
                .where(orm.AgentProfileRow.target_id == target_id)
                .order_by(orm.AgentProfileRow.created_at.desc())
            )
            return AgentProfile.model_validate(row.profile) if row else None

    def profile_for_run(self, run_id: str) -> AgentProfile | None:
        with self.db.session() as s:
            row = s.scalar(
                select(orm.AgentProfileRow)
                .where(orm.AgentProfileRow.run_id == run_id)
                .order_by(orm.AgentProfileRow.created_at.desc())
            )
            return AgentProfile.model_validate(row.profile) if row else None

    # ----------------------------------------------------------------- suites
    @staticmethod
    def suite_hash(tests: list[TestCase]) -> str:
        stable = []
        for t in sorted(tests, key=lambda x: x.id):
            d = t.model_dump(mode="json", exclude={"status", "rationale"})
            stable.append(d)
        return canonical_hash(stable)

    def save_suite(
        self,
        project_id: str,
        target_id: str | None,
        name: str,
        kind: str,
        tests: list[TestCase],
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        h = self.suite_hash(tests)
        with self.db.session() as s:
            prior = s.scalars(
                select(orm.TestSuiteRow).where(orm.TestSuiteRow.project_id == project_id, orm.TestSuiteRow.name == name)
            ).all()
            version = max((p.version for p in prior), default=0) + 1
            row = orm.TestSuiteRow(
                project_id=project_id,
                target_id=target_id,
                name=name,
                kind=kind,
                suite_hash=h,
                plan=plan or {},
                version=version,
            )
            s.add(row)
            s.flush()
            for t in tests:
                s.add(
                    orm.TestCaseRow(
                        suite_id=row.id,
                        test_key=t.id,
                        category=t.category,
                        risk_level=t.risk_level.value,
                        definition=t.model_dump(mode="json"),
                    )
                )
            s.flush()
            return row_to_dict(row)

    def get_suite(self, suite_id: str) -> tuple[dict[str, Any], list[TestCase]]:
        with self.db.session() as s:
            row = s.get(orm.TestSuiteRow, suite_id)
            if row is None:
                raise NotFoundError(f"test suite '{suite_id}' not found")
            cases = [TestCase.model_validate(c.definition) for c in sorted(row.cases, key=lambda c: c.test_key)]
            return row_to_dict(row), cases

    def list_suites(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(orm.TestSuiteRow).order_by(orm.TestSuiteRow.created_at)
            if project_id:
                q = q.where(orm.TestSuiteRow.project_id == project_id)
            return [row_to_dict(r) for r in s.scalars(q)]

    # ----------------------------------------------------------------- runs
    def create_run(
        self,
        project_id: str,
        target_id: str | None,
        suite_id: str | None,
        mode: str,
        manifest: dict[str, Any],
        limits: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a run. A run id that already exists as a row that has not started executing (the API queues a run before
        a worker takes it, and a worker marks it running while it discovers and plans) is taken over: its target, mode,
        manifest and limits are filled in and nothing is duplicated."""
        with self.db.session() as s:
            queued = s.get(orm.TestRunRow, run_id) if run_id else None
            if queued is not None:
                if queued.status not in {"pending", "running"} or queued.started_at is not None:
                    raise UserError(f"run '{run_id}' already exists")
                queued.project_id, queued.target_id, queued.suite_id, queued.mode = (
                    project_id,
                    target_id,
                    suite_id,
                    mode,
                )
                queued.manifest, queued.limits = {**(queued.manifest or {}), **manifest}, limits
                queued.version += 1
                s.flush()
                return row_to_dict(queued)
            row = orm.TestRunRow(
                id=run_id or new_id(),
                project_id=project_id,
                target_id=target_id,
                suite_id=suite_id,
                mode=mode,
                manifest=manifest,
                limits=limits,
                status="pending",
            )
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(orm.TestRunRow, run_id)
            if row is None:
                raise NotFoundError(f"run '{run_id}' not found")
            for k, v in fields.items():
                setattr(row, k, v)
            row.version += 1
            s.flush()
            return row_to_dict(row)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(orm.TestRunRow, run_id)
            if row is None:
                raise NotFoundError(f"run '{run_id}' not found")
            return row_to_dict(row)

    def resolve_run_id(self, ident: str) -> str:
        """An exact run id, or the one stored run whose id starts with ``ident`` (what a person copies from a listing)."""
        try:
            return str(self.get_run(ident)["id"])
        except UserError:
            pass
        if len(ident) < 4:
            raise NotFoundError(f"run '{ident}' not found")
        with self.db.session() as s:
            hits = list(s.scalars(select(orm.TestRunRow.id).where(orm.TestRunRow.id.startswith(ident)).limit(3)))
        if len(hits) == 1:
            return str(hits[0])
        if not hits:
            raise NotFoundError(f"run '{ident}' not found (see `agentlab runs list`)")
        raise UserError(f"'{ident}' matches several runs; give more of the id")

    def list_runs(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(orm.TestRunRow).order_by(orm.TestRunRow.created_at.desc()).limit(limit)
            if project_id:
                q = q.where(orm.TestRunRow.project_id == project_id)
            return [row_to_dict(r) for r in s.scalars(q)]

    def count_runs(self, status: str) -> int:
        """How many runs have this status, in any project."""
        with self.db.session() as s:
            return int(
                s.scalar(select(func.count()).select_from(orm.TestRunRow).where(orm.TestRunRow.status == status)) or 0
            )

    # ----------------------------------------------------------------- results / traces / events
    def save_result(self, result: TestResult) -> None:
        data = result.model_dump(mode="json")
        with self.db.session() as s:
            row = s.scalar(
                select(orm.TestResultRow).where(
                    orm.TestResultRow.run_id == result.run_id, orm.TestResultRow.test_key == result.test_id
                )
            )
            if row is None:
                row = orm.TestResultRow(
                    id=result.id, run_id=result.run_id, test_key=result.test_id, category=result.category
                )
                s.add(row)
            row.status = result.status.value
            row.score, row.confidence = result.score, result.confidence
            row.severity = result.severity.value if result.severity else None
            row.error_kind = result.error_kind.value if result.error_kind else None
            row.root_cause = result.root_cause.value if result.root_cause else None
            row.blocked_reason = result.blocked_reason
            row.latency_ms, row.tokens, row.cost_usd = result.latency_ms, result.tokens, result.cost_usd
            row.result = data

    def list_results(self, run_id: str) -> list[TestResult]:
        with self.db.session() as s:
            rows = s.scalars(
                select(orm.TestResultRow).where(orm.TestResultRow.run_id == run_id).order_by(orm.TestResultRow.test_key)
            ).all()
            return [TestResult.model_validate(r.result) for r in rows]

    def save_trace(self, trace: Trace, artifact_id: str | None) -> None:
        with self.db.session() as s:
            s.add(
                orm.TraceRow(
                    id=trace.id,
                    run_id=trace.run_id,
                    test_key=trace.test_id,
                    attempt=trace.attempt,
                    event_count=len(trace.events),
                    artifact_id=artifact_id,
                    summary={"types": sorted({e.type.value for e in trace.events})},
                )
            )

    def list_traces(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                row_to_dict(r)
                for r in s.scalars(
                    select(orm.TraceRow)
                    .where(orm.TraceRow.run_id == run_id)
                    .order_by(orm.TraceRow.test_key, orm.TraceRow.attempt)
                )
            ]

    def add_event(self, ev: Event) -> None:
        with self.db.session() as s:
            if s.get(orm.EventRow, ev.event_id) is None:
                s.add(
                    orm.EventRow(
                        event_id=ev.event_id,
                        run_id=ev.run_id,
                        test_id=ev.test_id,
                        timestamp=ev.timestamp,
                        type=ev.type.value,
                        payload=ev.payload,
                        redaction_status=ev.redaction_status.value,
                    )
                )

    def list_events(
        self,
        run_id: str,
        *,
        after_ts: datetime | None = None,
        limit: int = 1000,
        types: list[str] | None = None,
        inclusive: bool = False,
    ) -> list[dict[str, Any]]:
        """Events of a run in time order. ``after_ts`` with ``inclusive=True`` also returns the events stamped exactly
        then, so a reader that follows a run can resume at its last timestamp and drop the ids it has already seen
        instead of missing events that were written in the same instant."""
        with self.db.session() as s:
            ident: Any = orm.EventRow.event_id
            if self.db.engine.dialect.name == "postgresql":
                ident = ident.collate("C")  # byte order, the same as the order a reader compares ids in
            q = (
                select(orm.EventRow)
                .where(orm.EventRow.run_id == run_id)
                .order_by(orm.EventRow.timestamp, ident)
                .limit(limit)
            )
            if after_ts:
                after_ts = self._db_time(after_ts)
                q = q.where(orm.EventRow.timestamp >= after_ts if inclusive else orm.EventRow.timestamp > after_ts)
            if types:
                q = q.where(orm.EventRow.type.in_(types))
            return [row_to_dict(r) for r in s.scalars(q)]

    def _db_time(self, value: datetime) -> datetime:
        """A moment as the database holds them: SQLite keeps UTC wall-clock time without an offset, the others keep it."""
        utc = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return utc.replace(tzinfo=None) if self.db.engine.dialect.name == "sqlite" else utc

    def event_counts(self, run_id: str) -> dict[str, int]:
        """How many events of each type a run has recorded (what a progress display needs, without reading them all)."""
        with self.db.session() as s:
            rows = s.execute(
                select(orm.EventRow.type, func.count()).where(orm.EventRow.run_id == run_id).group_by(orm.EventRow.type)
            ).all()
            return {str(t): int(n) for t, n in rows}

    # ----------------------------------------------------------------- findings / scorecards / reports
    def save_findings(self, run_id: str, findings: list[Finding]) -> None:
        with self.db.session() as s:
            for f in findings:
                if s.get(orm.FindingRow, f.id):
                    continue
                s.add(
                    orm.FindingRow(
                        id=f.id,
                        run_id=run_id,
                        test_key=f.test_id,
                        severity=f.severity.value,
                        category=f.category,
                        title=f.title[:300],
                        is_security=f.is_security,
                        confidence=f.confidence,
                        finding=f.model_dump(mode="json"),
                    )
                )

    def list_findings(self, run_id: str) -> list[Finding]:
        with self.db.session() as s:
            rows = s.scalars(select(orm.FindingRow).where(orm.FindingRow.run_id == run_id)).all()
            return [Finding.model_validate(r.finding) for r in rows]

    def save_scorecard(self, run_id: str, sc: Scorecard) -> None:
        with self.db.session() as s:
            s.add(
                orm.ScorecardRow(
                    run_id=run_id,
                    profile=sc.profile,
                    overall=sc.overall,
                    confidence=sc.overall_confidence,
                    scorecard=sc.model_dump(mode="json"),
                )
            )

    def latest_scorecard(self, run_id: str) -> Scorecard | None:
        with self.db.session() as s:
            row = s.scalar(
                select(orm.ScorecardRow)
                .where(orm.ScorecardRow.run_id == run_id)
                .order_by(orm.ScorecardRow.created_at.desc())
            )
            return Scorecard.model_validate(row.scorecard) if row else None

    def save_report(self, run_id: str, formats: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        with self.db.session() as s:
            prior = s.scalars(select(orm.ReportRow).where(orm.ReportRow.run_id == run_id)).all()
            row = orm.ReportRow(run_id=run_id, report_version=len(prior) + 1, formats=formats, manifest=manifest)
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def get_report(self, report_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(orm.ReportRow, report_id)
            if row is None:
                raise NotFoundError(f"report '{report_id}' not found")
            return row_to_dict(row)

    def list_reports(self, run_id: str) -> list[dict[str, Any]]:
        """Every report version of a run, newest first."""
        with self.db.session() as s:
            rows = s.scalars(
                select(orm.ReportRow)
                .where(orm.ReportRow.run_id == run_id)
                .order_by(orm.ReportRow.report_version.desc())
            )
            return [row_to_dict(r) for r in rows]

    def latest_report(self, run_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.scalar(
                select(orm.ReportRow).where(orm.ReportRow.run_id == run_id).order_by(orm.ReportRow.created_at.desc())
            )
            return row_to_dict(row) if row else None

    # ----------------------------------------------------------------- artifacts / browser / evaluations
    def register_artifact(self, ref: ArtifactRef, uri: str = "") -> None:
        with self.db.session() as s:
            s.add(
                orm.ArtifactRow(
                    sha256=ref.sha256,
                    kind=ref.kind,
                    media_type=ref.media_type,
                    size=ref.size,
                    sensitivity=ref.sensitivity,
                    run_id=ref.run_id,
                    test_key=ref.test_key,
                    name=ref.name,
                    uri=uri,
                    meta=ref.meta,
                )
            )

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [row_to_dict(r) for r in s.scalars(select(orm.ArtifactRow).where(orm.ArtifactRow.run_id == run_id))]

    def save_browser_session(
        self,
        run_id: str,
        test_key: str,
        browser: str,
        trace_artifact_id: str | None,
        video_artifact_id: str | None,
        screenshots: list[str],
        actions: list[Any],
        meta: dict[str, Any],
    ) -> None:
        with self.db.session() as s:
            s.add(
                orm.BrowserSessionRow(
                    run_id=run_id,
                    test_key=test_key,
                    browser=browser,
                    trace_artifact_id=trace_artifact_id,
                    video_artifact_id=video_artifact_id,
                    screenshot_artifact_ids=screenshots,
                    actions=actions,
                    meta=meta,
                )
            )

    def list_browser_sessions(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                row_to_dict(r)
                for r in s.scalars(select(orm.BrowserSessionRow).where(orm.BrowserSessionRow.run_id == run_id))
            ]

    def ensure_evaluator(self, name: str, type_: str, version: str, config: dict[str, Any]) -> str:
        with self.db.session() as s:
            row = s.scalar(
                select(orm.EvaluatorRow).where(
                    orm.EvaluatorRow.name == name, orm.EvaluatorRow.evaluator_version == version
                )
            )
            if row is None:
                row = orm.EvaluatorRow(name=name, type=type_, evaluator_version=version, config=config)
                s.add(row)
                s.flush()
            return row.id

    def save_evaluations(self, result_id: str, items: list[dict[str, Any]]) -> None:
        with self.db.session() as s:
            for it in items:
                s.add(orm.EvaluationRow(result_id=result_id, **it))

    # ----------------------------------------------------------------- reviews
    def add_review(
        self,
        run_id: str,
        subject_type: str,
        subject_id: str,
        decision: str,
        reviewer: str,
        reason: str,
        original: dict[str, Any],
        reviewed: dict[str, Any],
        comment: str = "",
    ) -> dict[str, Any]:
        with self.db.session() as s:
            row = orm.ReviewRow(
                run_id=run_id,
                subject_type=subject_type,
                subject_id=subject_id,
                decision=decision,
                reviewer=reviewer,
                reason=reason,
                original=original,
                reviewed=reviewed,
                comment=comment,
            )
            s.add(row)
            s.flush()
            return row_to_dict(row)

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                row_to_dict(r)
                for r in s.scalars(
                    select(orm.ReviewRow).where(orm.ReviewRow.run_id == run_id).order_by(orm.ReviewRow.created_at)
                )
            ]


def open_store(url: str, *, migrate: bool = True) -> Store:
    """``migrate=True``: bring the schema to the current version. ``migrate=False``: create the tables directly (an
    in-memory database for tests). A caller that must not touch the schema builds ``Store(Database(url))``."""
    db = Database(url)
    if migrate:
        db.migrate()
    else:
        db.create_all()
    return Store(db)


__all__ = ["Database", "Store", "canonical_hash", "normalise_database_url", "open_store", "row_to_dict", "utcnow"]
