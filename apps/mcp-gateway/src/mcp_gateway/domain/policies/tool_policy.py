"""Tool classes, default policies, manifest verification and the invocation decision (Section 14).

Pure functions over plain values: every rule here is unit-tested without a server.

* **Declared, then verified.** A server is registered with the tools the tenant intends to use
  and their classes. Approval compares that manifest with the live server: every declared tool
  must exist, and the server's own annotations may only make a class stricter (`readOnlyHint:
  false` cannot be declared read-class). Undeclared tools are never reachable.
* **Grants decide.** Invoking needs an approved server, a tool whose policy is
  `require_grant`, and a grant to the caller's role or to the caller (Section 9). `allow` is
  reserved and not honoured; `deny` blocks a tool outright.
* **Write and admin tools** (Phase A10, Section 14) also need a fresh step-up to be granted
  and at every invocation; an `admin` tool also needs the caller to be an `org_admin`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, get_args

ToolClass = Literal["read_metadata", "read_data", "external_read", "write", "admin"]
Policy = Literal["allow", "require_grant", "deny"]
DenialReason = Literal[
    "server_not_approved",
    "tool_denied_by_policy",
    "not_granted",
    "admin_only",
    "step_up_required",
    "destination_not_allowed",
]

TOOL_CLASSES: Final = frozenset(get_args(ToolClass))
READ_CLASSES: Final = frozenset({"read_metadata", "read_data", "external_read"})
#: Classes whose grant and every invocation need a fresh step-up (Sections 7.3, 14).
STEP_UP_CLASSES: Final = frozenset({"write", "admin"})
#: MCP tool names (2025-06-18 spec guidance): letters, digits, `_`, `-`, `.`; at most 128.
TOOL_NAME: Final = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_TOOLS: Final = 100


def default_policy(tool_class: str) -> Policy:
    """Every class needs a grant; write/admin add step-up and role checks (Phase A10)."""
    del tool_class
    return "require_grant"


def needs_step_up(tool_class: str) -> bool:
    return tool_class in STEP_UP_CLASSES


def manifest_problems(declared: Sequence[tuple[str, str]]) -> list[str]:
    """Problems with a declared manifest as submitted (names, classes, duplicates, size)."""
    if not declared:
        return ["declare at least one tool"]
    if len(declared) > MAX_TOOLS:
        return [f"at most {MAX_TOOLS} tools"]
    problems: list[str] = []
    seen: set[str] = set()
    for name, tool_class in declared:
        if not TOOL_NAME.match(name):
            problems.append(f"{name[:64]!r}: invalid tool name")
        elif name in seen:
            problems.append(f"{name}: declared twice")
        if tool_class not in TOOL_CLASSES:
            problems.append(f"{name[:64]}: unknown tool class")
        seen.add(name)
    return problems


@dataclass(frozen=True)
class DiscoveredTool:
    name: str
    #: The server's `annotations.readOnlyHint`, or None when it does not say.
    read_only_hint: bool | None = None


def verification_problems(
    declared: Mapping[str, str], discovered: Iterable[DiscoveredTool]
) -> list[str]:
    """What the live server contradicts in the declared manifest (empty = verified)."""
    live = {tool.name: tool for tool in discovered}
    problems: list[str] = []
    for name, tool_class in sorted(declared.items()):
        tool = live.get(name)
        if tool is None:
            problems.append(f"{name}: not offered by the server")
        elif tool_class in READ_CLASSES and tool.read_only_hint is False:
            problems.append(f"{name}: the server marks it as not read-only")
    return problems


@dataclass(frozen=True)
class GrantRef:
    grantee_role: str | None
    grantee_user_id: uuid.UUID | None


def denial(
    *,
    server_status: str,
    tool_class: str,
    policy: str,
    grants: Iterable[GrantRef],
    user_id: uuid.UUID,
    roles: frozenset[str],
    step_up_fresh: bool,
) -> DenialReason | None:
    """Why this caller may not invoke the tool, checked in order; None when allowed.

    The grant comes before step-up: a caller who could never invoke the tool is not asked to
    prove their identity first."""
    if server_status != "approved":
        return "server_not_approved"
    # `allow` is reserved (Section 14): until a phase defines it, it is not honoured.
    if policy != "require_grant":
        return "tool_denied_by_policy"
    if not any(
        g.grantee_user_id == user_id or (g.grantee_role is not None and g.grantee_role in roles)
        for g in grants
    ):
        return "not_granted"
    if tool_class == "admin" and "org_admin" not in roles:
        return "admin_only"
    if needs_step_up(tool_class) and not step_up_fresh:
        return "step_up_required"
    return None
