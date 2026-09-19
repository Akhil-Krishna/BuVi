"""The Section 24 registry covers every checklist bullet and every reference is real (Phase A12)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_registry():  # type: ignore[no-untyped-def]
    path = Path(__file__).with_name("section_24.py")
    spec = importlib.util.spec_from_file_location("buvi_section_24", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


_REGISTRY_MODULE = _load_registry()
REGISTRY = _REGISTRY_MODULE.REGISTRY
Control = _REGISTRY_MODULE.Control

pytestmark = pytest.mark.security

SPEC = (ROOT / "docs" / "architecture" / "Agentic_BI_Platform_Build_Spec.md").read_text()


def _bullets() -> list[str]:
    section = SPEC.split("## 24. Security loophole checklist", 1)[1].split("\n---\n", 1)[0]
    bullets: list[str] = []
    continuing = False
    for line in section.splitlines():
        if line.startswith("- [ ] "):
            bullets.append(line[6:].strip())
            continuing = True
        elif continuing and line.startswith("      ") and line.strip():
            bullets[-1] += " " + line.strip()
        else:
            continuing = False
    return bullets


def test_every_bullet_is_covered_exactly_once() -> None:
    bullets = _bullets()
    assert len(bullets) >= 30, "Section 24 parse broke"
    for bullet in bullets:
        matches = [c for c in REGISTRY if bullet.startswith(c.item)]
        assert len(matches) == 1, f"Section 24 bullet not (uniquely) in the registry: {bullet[:80]}"
    assert len(REGISTRY) == len(bullets), "the registry lists a control the spec no longer has"


@pytest.mark.parametrize("control", REGISTRY, ids=lambda c: c.item[:48])
def test_every_control_is_enforced_or_explicitly_deferred(control: object) -> None:
    assert isinstance(control, Control)
    assert control.enforced_by or control.deferred, control.item
    for reference in control.enforced_by:
        path, _, name = reference.partition("::")
        source = ROOT / path
        assert source.is_file(), f"{control.item}: missing {path}"
        assert re.search(rf"^(async )?def {re.escape(name)}\(", source.read_text(), re.M), (
            f"{control.item}: {name} not found in {path}"
        )
    if control.deferred:
        phase, _, what = control.deferred.partition(": ")
        assert phase == "Phase C1" and what, control.deferred
        c1 = SPEC.split("### Phase C1", 1)[1].split("### Phase C2", 1)[0]
        keywords = {
            "MCP": "MCP invocation",
            "scanning": "image scanning",
            "OIDC": "OIDC",
            "digests": "digests",
            "tenant-deletion": "tenant-deletion",
        }
        hit = [k for k in keywords if k in what]
        assert hit and all(keywords[k] in c1 for k in hit), f"not tracked in Phase C1: {what}"
