"""Tool classes, default policies, manifest verification and the invocation decision (Section 14).

Pure functions over plain values: every rule here is unit-tested without a server.

* **Declared, then verified.** A server is registered with the tools the tenant intends to use
  and their classes. Approval compares that manifest with the live server: every declared tool
  must exist, and the server's own annotations may only make a class stricter (`readOnlyHint:
  false` cannot be declared read-class). Undeclared tools are never reachable.
* **Grants decide.** Invoking needs an approved server, a tool whose policy is not `deny`, and a
  grant to the caller's role or to the caller (Section 9). Read classes default to
  `require_grant`; `write`/`admin` to `deny` until Phase A10. `allow` is reserved.
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
    "server_not_approved", "tool_denied_by_policy", "not_granted", "destination_not_allowed"
]

TOOL_CLASSES: Final = frozenset(get_args(ToolClass))
READ_CLASSES: Final = frozenset({"read_metadata", "read_data", "external_read"})
#: MCP tool names (2025-06-18 spec guidance): letters, digits, `_`, `-`, `.`; at most 128.
TOOL_NAME: Final = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_TOOLS: Final = 100


def default_policy(tool_class: str) -> Policy:
    return "require_grant" if tool_class in READ_CLASSES else "deny"


def is_grantable(tool_class: str) -> bool:
    """Write/admin grants wait for Phase A10's per-invocation step-up confirmation."""
    return tool_class in READ_CLASSES


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
    policy: str,
    grants: Iterable[GrantRef],
    user_id: uuid.UUID,
    roles: frozenset[str],
) -> DenialReason | None:
    """Why this caller may not invoke the tool, checked in order; None when allowed."""
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
    return None
