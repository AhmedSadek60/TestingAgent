import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from agentlab.core.enums import RiskClass, Severity, TestStatus
from agentlab.core.models import AssertionSpec, Finding, TargetSpec, TestCase, TestResult
from agentlab.security.credentials import CredentialProfile
from agentlab.security.redactor import get_redactor
from agentlab.storage import orm
from agentlab.storage.artifacts import LocalArtifactStore, MemoryArtifactStore
from agentlab.storage.db import Database, Store, open_store
from agentlab.storage.migrate import upgrade

PG = os.environ.get("AGENTLAB_TEST_POSTGRES_URL")


def case(i, cat="functional"):
    return TestCase(id=f"FUNC-{i:03d}", name="n", category=cat, objective="o", input="hi",
                    assertions=[AssertionSpec(type="contains", params={"value": "x"})], risk_level=RiskClass.SAFE)


def test_migrations_create_schema_matching_models(tmp_path):
    url = f"sqlite:///{tmp_path}/m.db"
    upgrade(url)
    eng = create_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), orm.Base.metadata)
    assert diff == [], f"models and migrations drifted: {diff}"
    names = set(inspect(eng).get_table_names())
    for expected in ("projects", "targets", "repositories", "agent_profiles", "credential_profiles", "documents",
                     "document_versions", "skills", "test_suites", "test_cases", "test_runs", "test_results",
                     "traces", "artifacts", "evaluators", "evaluations", "findings", "scorecards", "reports",
                     "model_providers", "model_configurations", "browser_sessions", "reviews", "events"):
        assert expected in names


@pytest.mark.postgres
@pytest.mark.skipif(not PG, reason="AGENTLAB_TEST_POSTGRES_URL not set")
def test_migrations_and_store_on_postgres():
    eng = create_engine(PG)
    with eng.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    upgrade(PG)
    with eng.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), orm.Base.metadata) == []
    store = Store(Database(PG))
    p = store.create_project("pg-project")
    t = store.add_target(p["id"], TargetSpec(name="t", mock={"behaviors": ["success"]}))
    suite = store.save_suite(p["id"], t["id"], "s", "full", [case(1)])
    run = store.create_run(p["id"], t["id"], suite["id"], "full", {"a": 1}, {})
    store.save_result(TestResult(run_id=run["id"], test_id="FUNC-001", test_name="n", category="functional",
                                 score_category="functional_quality", status=TestStatus.PASSED, score=1.0))
    assert store.list_results(run["id"])[0].status == TestStatus.PASSED


def test_store_roundtrip_and_versioning():
    store = open_store("sqlite://", migrate=False)
    p = store.create_project("demo", "d", "obj")
    with pytest.raises(Exception, match="already exists"):
        store.create_project("demo")
    t = store.add_target(p["id"], TargetSpec(name="bot", mock={"behaviors": ["success"]}, version="1.0"))
    assert t["kind"] == "mock" and store.get_target(t["id"])[1].version == "1.0"
    t2 = store.add_target(p["id"], TargetSpec(name="bot", mock={"behaviors": ["success"]}, version="1.1"))
    assert t2["id"] == t["id"] and t2["version"] == 2
    s1 = store.save_suite(p["id"], t["id"], "suite", "full", [case(1), case(2)])
    s2 = store.save_suite(p["id"], t["id"], "suite", "full", [case(1), case(2)])
    s3 = store.save_suite(p["id"], t["id"], "suite", "full", [case(1), case(3)])
    assert s1["suite_hash"] == s2["suite_hash"] != s3["suite_hash"] and s3["version"] == 3
    _, cases = store.get_suite(s1["id"])
    assert [c.id for c in cases] == ["FUNC-001", "FUNC-002"]
    run = store.create_run(p["id"], t["id"], s1["id"], "full", {"agentlab": "0.1"}, {"max_cost_usd": 1})
    res = TestResult(run_id=run["id"], test_id="FUNC-001", test_name="n", category="functional",
                     score_category="functional_quality", status=TestStatus.BLOCKED, blocked_reason="no creds")
    store.save_result(res)
    res.status = TestStatus.PASSED
    store.save_result(res)  # upsert by (run, test)
    assert [r.status for r in store.list_results(run["id"])] == [TestStatus.PASSED]
    f = Finding(run_id=run["id"], test_id="FUNC-001", title="x", category="functional", severity=Severity.HIGH,
                confidence=0.9, expected="a", observed="b", impact="c", reproduction="d", recommendation="e")
    store.save_findings(run["id"], [f])
    store.save_findings(run["id"], [f])
    assert len(store.list_findings(run["id"])) == 1
    d = store.add_document(p["id"], "policy.md", "text/markdown", "a" * 64, 10, None, {})
    d2 = store.add_document(p["id"], "policy.md", "text/markdown", "a" * 64, 10, None, {})
    d3 = store.add_document(p["id"], "policy.md", "text/markdown", "b" * 64, 12, None, {})
    assert d["new_version"] and not d2["new_version"] and d3["version"] == 2
    store.add_review(run["id"], "finding", f.id, "false_positive", "alice", "why", {"severity": "high"}, {"status": "fp"})
    assert store.get_run(run["id"])["status"] == "pending"
    assert store.list_findings(run["id"])[0].severity == Severity.HIGH  # original never mutated by review


def test_credential_metadata_never_stores_secret_values():
    store = open_store("sqlite://", migrate=False)
    prof = CredentialProfile(name="tester", kind="bearer", scopes=["example.com"], references={"token": "env:X"})
    store.upsert_credential_meta(prof)
    dumped = str(store.list_credential_meta())
    assert "env:X" not in dumped and "tester" in dumped


def test_local_artifact_store_content_addressing_redaction_and_permissions(tmp_path):
    get_redactor().register_secret("super-secret-value-12345", "test")
    store = LocalArtifactStore(tmp_path / "art")
    a = store.put("token is super-secret-value-12345", kind="log", media_type="text/plain", run_id="r1")
    b = store.put("token is super-secret-value-12345", kind="log", media_type="text/plain", run_id="r1")
    assert a.id == b.id and a.redacted and b"super-secret" not in store.get(a.id)
    shot = store.put(b"\x89PNG....", kind="screenshot", media_type="image/png", sensitivity="restricted", run_id="r1")
    path = store.local_path(shot.id)
    assert "restricted" in str(path) and (path.stat().st_mode & 0o077) == 0
    assert {r.id for r in store.list("r1")} == {a.id, shot.id}
    with pytest.raises(Exception):
        store.get("sha256-../../etc/passwd")
    with pytest.raises(Exception):
        store.get("../../etc/passwd")


def test_memory_artifact_store():
    m = MemoryArtifactStore()
    ref = m.put_json({"a": 1}, kind="json")
    assert m.get_json(ref.id) == {"a": 1}
