"""Phase A0 conformance tests: the monorepo matches Section 4 of the build spec.

These guard the scaffold itself -- the directory contract every later phase
builds into, and the Section 37 rules that are checkable statically. Service
behaviour is tested inside each `apps/<service>/src/*/tests/` tree from Phase A1.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Section 4: the exact top-level folder structure.
REQUIRED_DIRECTORIES = [
    "apps",
    "web/next-app",
    "packages/python",
    "packages/ts",
    "infra/docker",
    "infra/compose",
    "infra/kubernetes",
    "infra/terraform",
    "contracts/openapi",
    "contracts/events",
    "contracts/json-schema",
    "docs/architecture",
    "docs/adr",
    "docs/runbooks",
    "scripts",
    ".github/workflows",
]

REQUIRED_FILES = [
    "Makefile",
    "pyproject.toml",
    "uv.lock",
    "ruff.toml",
    ".gitignore",
    "README.md",
    ".python-version",
    ".github/workflows/ci.yml",
    "docs/adr/0001-architecture-baseline.md",
]

# Section 4: the shared Python packages, with their importable module names.
# `platform-secrets` was extracted in Phase A3 (ADR 0004), `platform-egress` in Phase A4 (ADR 0005).
PLATFORM_PACKAGES = {
    "platform-contracts": "platform_contracts",
    "platform-observability": "platform_observability",
    "platform-auth": "platform_auth",
    "platform-testing": "platform_testing",
    "platform-secrets": "platform_secrets",
    "platform-egress": "platform_egress",
}


@pytest.mark.parametrize("relative_path", REQUIRED_DIRECTORIES)
def test_required_directory_exists(relative_path: str) -> None:
    assert (REPO_ROOT / relative_path).is_dir(), f"Section 4 requires {relative_path}/"


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(relative_path: str) -> None:
    assert (REPO_ROOT / relative_path).is_file(), f"Phase A0 requires {relative_path}"


@pytest.mark.parametrize(("package_dir", "module_name"), sorted(PLATFORM_PACKAGES.items()))
def test_platform_package_is_a_workspace_member(package_dir: str, module_name: str) -> None:
    package_root = REPO_ROOT / "packages" / "python" / package_dir
    assert (package_root / "pyproject.toml").is_file()
    assert (package_root / "src" / module_name / "__init__.py").is_file()
    assert (package_root / "src" / module_name / "py.typed").is_file(), (
        f"{package_dir} must ship py.typed so consumers get its types under mypy"
    )
    assert (package_root / "README.md").read_text().strip(), (
        f"{package_dir} needs a README documenting its narrow contract (Section 37)"
    )


def test_root_is_a_virtual_uv_workspace_root() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert config["tool"]["uv"]["workspace"]["members"] == ["apps/*", "packages/python/*"]
    assert config["tool"]["uv"]["package"] is False, (
        "The workspace root defines the workspace; it is not itself a distributable package."
    )
    assert "build-system" not in config


def test_python_is_pinned_to_312_repo_wide() -> None:
    assert (REPO_ROOT / ".python-version").read_text().strip().startswith("3.12")
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert config["project"]["requires-python"] == ">=3.12,<3.13"


def test_gitignore_excludes_venv_node_modules_and_env() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text()
    for entry in (".venv/", "node_modules/", ".env"):
        assert entry in ignored, f"Phase A0 requires .gitignore to exclude {entry}"


def test_no_shared_utils_or_common_dumping_ground() -> None:
    """Section 37: shared code lives in a named platform-* package, not a junk drawer."""
    banned = {"utils.py", "util.py", "common.py", "helpers.py", "misc.py"}
    searched = [REPO_ROOT / "apps", REPO_ROOT / "packages" / "python"]
    offenders = [
        path.relative_to(REPO_ROOT)
        for root in searched
        for path in root.rglob("*.py")
        if path.name in banned or path.parent.name in {"utils", "common"}
    ]
    assert not offenders, f"Section 37 forbids shared dumping grounds: {offenders}"


LIVE_FLOWS = sorted((REPO_ROOT / "scripts").glob("test-*.sh"))


@pytest.mark.parametrize("flow", LIVE_FLOWS, ids=lambda path: path.name)
def test_every_live_flow_starts_from_the_shared_reset(flow: Path) -> None:
    """A flow that fails midway must not change how the next one behaves (a failed A10 run once
    left a tenant policy that broke A1): every flow runs `reset_demo_state` -- which undoes and
    then verifies all cross-flow state -- before it starts a service or a check."""
    lines = [line.strip() for line in flow.read_text().splitlines()]
    assert any(line.startswith("source ") and "live-flow.sh" in line for line in lines), flow
    resets = [i for i, line in enumerate(lines) if line.split(" ")[0] == "reset_demo_state"]
    assert resets, f"{flow.name} does not call reset_demo_state"
    first_action = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(("start_service", "uv run", "GATEWAY_URL="))
    )
    assert resets[0] < first_action, f"{flow.name} runs something before reset_demo_state"


def test_every_live_flow_is_in_test_live() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text()
    target = makefile.split("\ntest-live:", 1)[1].split("\n\n", 1)[0]
    missing = [flow.name for flow in LIVE_FLOWS if f"scripts/{flow.name}" not in target]
    assert not missing, f"make test-live does not run: {missing}"
