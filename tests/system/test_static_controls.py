"""Section 24 controls checkable from the repository alone (Phase A12; no running stack)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
APPS = sorted(p for p in (ROOT / "apps").iterdir() if (p / "pyproject.toml").is_file())

#: Each service's own Postgres schema (Section 8); services not listed own none.
OWN_SCHEMA = {
    "identity-service": "identity",
    "metadata-service": "metadata",
    "semantic-service": "semantic",
    "analytics-orchestrator": "analytics",
    "query-gateway": "query_gateway",
    "dashboard-service": "dashboard",
    "mcp-gateway": "mcp",
    "notification-service": "notification",
}
SCHEMAS = set(OWN_SCHEMA.values())


def _sources(app: Path) -> list[Path]:
    roots = [app / "src", app / "migrations"]
    return [
        f
        for root in roots
        if root.is_dir()
        for f in root.rglob("*.py")
        if "/tests/" not in f.as_posix()
    ]


def test_no_service_touches_another_services_schema() -> None:
    """Section 33: call the owning service's API, never its tables."""
    names = "|".join(sorted(SCHEMAS))
    pattern = re.compile(
        # `FROM identity.users`-style table references, or a SQLAlchemy/Alembic `schema="x"`.
        rf"(?:\b(?:FROM|JOIN|INTO|UPDATE|TABLE|ON)\s+({names})\.[a-z_]"
        rf"|schema\s*=\s*[\"']({names})[\"'])",
        re.IGNORECASE,
    )
    offences: list[str] = []
    for app in APPS:
        own = OWN_SCHEMA.get(app.name)
        for source in _sources(app):
            for number, line in enumerate(source.read_text().splitlines(), 1):
                for match in pattern.finditer(line):
                    if (match.group(1) or match.group(2)).lower() != own:
                        offences.append(f"{source.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offences, "cross-service schema access:\n" + "\n".join(offences)


def test_no_dynamic_code_execution_in_any_service() -> None:
    """Section 24: no eval/exec/compile/subprocess anywhere a request can reach."""
    pattern = re.compile(
        r"(?<![\w.])(?:eval|exec|compile|__import__)\(|\bsubprocess\b|\bos\.system\(|\bpickle\.loads?\("
    )
    offences = [
        f"{source.relative_to(ROOT)}:{n}: {line.strip()}"
        for app in APPS
        for source in _sources(app)
        for n, line in enumerate(source.read_text().splitlines(), 1)
        if pattern.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offences, "dynamic code execution:\n" + "\n".join(offences)


def test_every_service_image_runs_as_non_root_with_a_healthcheck() -> None:
    for app in APPS:
        dockerfile = app / "Dockerfile"
        assert dockerfile.is_file(), f"{app.name} has no Dockerfile"
        text = dockerfile.read_text()
        users = re.findall(r"^USER\s+(\S+)", text, re.M)
        assert users and users[-1] not in ("root", "0"), f"{app.name} runs as root"
        assert re.search(r"^HEALTHCHECK\s", text, re.M), f"{app.name} has no HEALTHCHECK"


def _workflows() -> str:
    return "\n".join(p.read_text() for p in (ROOT / ".github" / "workflows").glob("*.yml"))


def test_ci_runs_dependency_audits() -> None:
    workflows = _workflows()
    assert "pip-audit" in workflows or "osv-scanner" in workflows
    assert re.search(r"npm(@[\d.]+)? audit", workflows)


def test_ci_holds_no_long_lived_cloud_credentials() -> None:
    forbidden = re.compile(
        r"secrets\.(AWS_|GCP_|GOOGLE_|AZURE_|GCLOUD|SERVICE_ACCOUNT)", re.IGNORECASE
    )
    assert not forbidden.search(_workflows())


@pytest.mark.parametrize(
    "contract",
    sorted((ROOT / "contracts" / "openapi").glob("*.json")),
    ids=lambda p: p.name,
)
def test_every_exported_openapi_document_is_valid(contract: Path) -> None:
    """Phase A12 DoD: every service exports a *valid* OpenAPI document."""
    from openapi_spec_validator import validate

    validate(json.loads(contract.read_text()))
