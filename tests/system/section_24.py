"""Section 24 checklist -> the tests that enforce each bullet (Phase A12).

Every bullet of the spec's Section 24 appears here exactly once, keyed by the start of its text.
`tests/system/test_section_24_registry.py` fails when:

- the spec gains a bullet this registry does not cover;
- a referenced test no longer exists (`file::test_name`, checked in the file's source);
- a bullet has neither enforcing tests nor a deferral.

A bullet may be partly enforced and partly deferred. A deferral names the phase that owns it; each
deferred control is listed in the spec's Phase C1 text, and the registry test checks that too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

A = "apps/{svc}/src/{mod}/tests"


def t(svc: str, path: str) -> str:
    return f"{A.format(svc=svc, mod=svc.replace('-', '_'))}/{path}"


STATIC = "tests/system/test_static_controls.py"
LIVE = "tests/system/test_live_controls.py"


@dataclass(frozen=True)
class Control:
    item: str  # the bullet's opening words, as written in Section 24
    enforced_by: tuple[str, ...] = ()
    deferred: str | None = None  # "Phase C1: <what>" when part of the bullet is not built yet
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


REGISTRY: tuple[Control, ...] = (
    # --- Access control ---------------------------------------------------------------------
    Control(
        "No endpoint authorizes on permission alone",
        (
            t(
                "identity-service",
                "integration/test_authorization_triplet.py::test_cross_tenant_resource_returns_404",
            ),
            t(
                "metadata-service",
                "integration/test_authorization_triplet.py::test_cross_tenant_writes_change_nothing",
            ),
            t(
                "query-gateway",
                "integration/test_authorization.py::test_cross_tenant_database_id_is_404_like_a_missing_one",
            ),
            t(
                "dashboard-service",
                "integration/test_share_links.py::test_an_org_admin_can_revoke_a_leaked_link_on_any_dashboard",
            ),
        ),
    ),
    Control(
        "No admin/step-up action is reachable without a fresh MFA",
        (
            t(
                "api-gateway",
                "unit/test_catalog.py::test_every_section_7_3_operation_requires_step_up_at_the_gateway",
            ),
            t("api-gateway", "integration/test_gateway.py::test_step_up_routes_refuse_stale_mfa"),
            t(
                "identity-service",
                "integration/test_authorization_triplet.py::test_step_up_endpoint_refuses_stale_mfa",
            ),
            t(
                "metadata-service",
                "integration/test_authorization_triplet.py::test_setting_credentials_requires_fresh_step_up",
            ),
            t(
                "notification-service",
                "integration/test_webhooks.py::test_webhook_management_needs_org_admin_and_step_up",
            ),
        ),
    ),
    Control(
        "Cross-tenant resource IDs return `404`",
        (
            t(
                "identity-service",
                "integration/test_authorization_triplet.py::test_cross_tenant_and_nonexistent_are_indistinguishable",
            ),
            t(
                "metadata-service",
                "integration/test_authorization_triplet.py::test_cross_tenant_resource_is_404_like_a_missing_one",
            ),
            t(
                "query-gateway",
                "integration/test_authorization.py::test_cross_tenant_database_id_is_404_like_a_missing_one",
            ),
            t(
                "dashboard-service",
                "integration/test_share_links.py::test_revoked_expired_and_unknown_tokens_are_the_same_404",
            ),
        ),
    ),
    Control(
        "Frontend route grouping/role checks are never the only gate",
        (
            t(
                "api-gateway",
                "integration/test_gateway.py::test_every_protected_route_requires_authentication",
            ),
            t("api-gateway", "integration/test_gateway.py::test_missing_permission_or_role_is_403"),
            t(
                "identity-service",
                "integration/test_internal_api.py::test_require_gateway_token_blocks_direct_calls",
            ),
            t(
                "query-gateway",
                "integration/test_public_sql.py::test_only_the_gateway_reaches_the_public_routes",
            ),
        ),
    ),
    Control(
        "The last `org_admin` of a tenant cannot be demoted or deleted",
        (
            t(
                "identity-service",
                "integration/test_lifecycle.py::test_last_active_org_admin_cannot_be_removed",
            ),
            t(
                "identity-service",
                "integration/test_lifecycle.py::test_admin_cannot_demote_or_delete_themselves",
            ),
        ),
    ),
    # --- Credentials and secrets -------------------------------------------------------------
    Control(
        "No customer database credential is ever sent to",
        (
            t(
                "metadata-service",
                "integration/test_secret_hygiene.py::test_credentials_never_leave_the_secret_store",
            ),
            t(
                "metadata-service",
                "integration/test_internal_query_policy.py::test_policy_returns_catalog_flags_and_the_vault_pointer_only",
            ),
        ),
    ),
    Control(
        "No secret is logged, included in an error response",
        (
            t(
                "identity-service",
                "unit/test_secrets_and_tokens.py::test_redact_masks_secret_shaped_keys",
            ),
            t(
                "identity-service",
                "integration/test_internal_api.py::test_service_records_audit_event_with_redaction_and_request_id",
            ),
            t(
                "metadata-service",
                "integration/test_secret_hygiene.py::test_credentials_never_leave_the_secret_store",
            ),
            t(
                "query-gateway",
                "unit/test_sql_validator.py::test_rejection_details_are_identifiers_never_parser_text",
            ),
        ),
    ),
    Control(
        "No shared root/admin DB credential is used for application query execution",
        (
            t(
                "query-gateway",
                "integration/test_queries.py::test_database_refuses_what_the_validator_would_have",
            ),
            f"{LIVE}::test_query_principals_are_read_only",
            f"{LIVE}::test_the_request_path_role_cannot_bypass_rls",
        ),
    ),
    Control(
        "API keys are stored hashed",
        (
            t(
                "identity-service",
                "unit/test_secrets_and_tokens.py::test_api_key_secret_is_hashed_with_argon2",
            ),
        ),
    ),
    # --- Injection and execution -------------------------------------------------------------
    Control(
        "No unrestricted Python/JS execution path exists",
        (f"{STATIC}::test_no_dynamic_code_execution_in_any_service",),
    ),
    Control(
        "All SQL passes through the AST allow-list validator",
        (
            t(
                "query-gateway",
                "unit/test_sql_validator.py::test_one_hundred_percent_of_the_unsafe_corpus_is_rejected",
            ),
            t(
                "query-gateway",
                "integration/test_queries.py::test_unsafe_sql_is_rejected_audited_and_never_reaches_the_database",
            ),
            t(
                "analytics-orchestrator",
                "integration/test_runs.py::test_rejected_sql_is_repaired_at_most_twice",
            ),
        ),
    ),
    Control(
        "ChartSpec has no field that accepts raw HTML/CSS/JS",
        (
            "packages/python/platform-contracts/tests/test_contracts.py::test_unknown_keys_and_markup_are_rejected",
            t(
                "visualization-service",
                "integration/test_validate_api.py::test_unknown_field_is_a_rejection_with_problems",
            ),
        ),
    ),
    Control(
        "All MCP tool output is treated as untrusted data",
        (
            t("mcp-gateway", "integration/test_invocation.py::test_the_dod_journey"),
            t("mcp-gateway", "integration/test_ssrf.py::test_hostile_response_bodies_are_refused"),
        ),
    ),
    # --- SSRF / outbound network ---------------------------------------------------------------
    Control(
        "Every outbound fetch initiated on a user's behalf re-validates",
        (
            t(
                "mcp-gateway",
                "integration/test_ssrf.py::test_dns_rebinding_after_approval_is_refused_and_recorded",
            ),
            t(
                "notification-service",
                "integration/test_webhooks.py::test_a_destination_that_rebinds_to_a_private_address_is_never_contacted",
            ),
            t("query-gateway", "integration/test_queries.py::test_executor_enforces_egress_policy"),
        ),
    ),
    Control(
        "Webhook and MCP endpoints are allow-listed per tenant",
        (
            t("mcp-gateway", "integration/test_ssrf.py::test_a_redirect_is_never_followed"),
            t(
                "mcp-gateway",
                "integration/test_ssrf.py::test_registration_refuses_what_the_url_reveals",
            ),
            t(
                "notification-service",
                "integration/test_webhooks.py::test_non_allow_listed_destinations_and_event_types_are_refused",
            ),
            t(
                "notification-service",
                "integration/test_webhooks.py::test_a_failing_receiver_gets_bounded_attempts_and_a_recorded_failure",
            ),
        ),
    ),
    # --- Multi-tenancy ---------------------------------------------------------------------------
    Control(
        "Every tenant-owned table has RLS enabled",
        (
            f"{LIVE}::test_every_service_table_has_forced_rls",
            f"{LIVE}::test_the_request_path_role_cannot_bypass_rls",
        ),
    ),
    Control(
        'No cross-service "just query the other service\'s database directly" shortcut',
        (f"{STATIC}::test_no_service_touches_another_services_schema",),
    ),
    # --- AI-specific --------------------------------------------------------------------------
    Control(
        "No stage of the Flow passes unbounded free text",
        (
            t(
                "analytics-orchestrator",
                "unit/test_model_router_and_policies.py::test_context_ranking_plan_checks_and_types",
            ),
            t(
                "analytics-orchestrator",
                "unit/test_model_router_and_policies.py::test_invalid_output_is_repaired_at_most_twice",
            ),
        ),
    ),
    Control(
        "Retrieved schema text, sample values, and MCP output are explicitly marked as data",
        (
            t(
                "analytics-orchestrator",
                "unit/test_model_router_and_policies.py::test_prompt_payload_is_rendered_as_tagged_data",
            ),
            t(
                "analytics-orchestrator",
                "integration/test_runs.py::test_prompts_carry_data_blocks_never_rows_or_credentials",
            ),
        ),
    ),
    Control(
        "Token/step/time budgets are enforced by the Flow itself",
        (
            t(
                "analytics-orchestrator",
                "unit/test_model_router_and_policies.py::test_budgets_are_enforced_before_the_call",
            ),
            t(
                "analytics-orchestrator",
                "integration/test_runs.py::test_run_budget_exceeded_before_any_call",
            ),
            t(
                "analytics-orchestrator",
                "integration/test_runs.py::test_tenant_daily_budget_is_enforced",
            ),
        ),
    ),
    Control(
        "Chain-of-thought / private reasoning is never shipped to the client",
        (
            "packages/python/platform-contracts/tests/test_contracts.py::test_run_event_wire_shape_matches_section_11",
            t(
                "analytics-orchestrator",
                "integration/test_runs.py::test_run_failures_are_typed_and_user_safe",
            ),
        ),
    ),
    # --- Rate limiting and abuse ------------------------------------------------------------------
    Control(
        "Auth endpoints (login, MFA verify, password reset) have per-account and per-IP rate limits",
        (
            t(
                "api-gateway",
                "unit/test_catalog.py::test_login_shaped_routes_use_the_strict_auth_tier",
            ),
            t(
                "api-gateway",
                "integration/test_gateway.py::test_auth_tier_is_independent_of_the_public_tier",
            ),
            t(
                "identity-service",
                "integration/test_webauthn_and_policies.py::test_mfa_guesses_are_limited_per_account_not_only_per_ip",
            ),
            f"{LIVE}::test_the_idp_locks_an_account_after_repeated_password_failures",
        ),
        notes="Passwords and password reset live in Keycloak (Section 6.1): its realm locks an "
        "account after 5 failures; the gateway's auth tier limits per IP.",
    ),
    Control(
        "`sql:execute` and MCP invocation both have per-tenant concurrency",
        (
            t(
                "query-gateway",
                "integration/test_concurrency_redis.py::test_the_cap_is_per_tenant_across_replicas",
            ),
            t(
                "query-gateway",
                "integration/test_queries.py::test_per_tenant_concurrency_cap_refuses_without_affecting_other_tenants",
            ),
            t(
                "api-gateway",
                "integration/test_gateway.py::test_tenant_bucket_caps_all_users_of_a_tenant",
            ),
        ),
        deferred="Phase C1: per-tenant MCP invocation concurrency and per-minute limits",
        notes="MCP calls already share the gateway's per-tenant rate bucket.",
    ),
    Control(
        "Export/download endpoints have row/byte thresholds",
        (
            t(
                "query-gateway",
                "integration/test_public_sql.py::test_reading_export_sized_results_needs_step_up",
            ),
            t(
                "dashboard-service",
                "integration/test_share_links.py::test_reading_export_sized_results_needs_step_up",
            ),
        ),
    ),
    # --- Supply chain / build -----------------------------------------------------------------------
    Control(
        "CI runs dependency vulnerability scanning",
        (f"{STATIC}::test_ci_runs_dependency_audits",),
        deferred="Phase C1: container image scanning that blocks on high/critical findings",
    ),
    Control(
        "No long-lived cloud credentials in repo secrets",
        (f"{STATIC}::test_ci_holds_no_long_lived_cloud_credentials",),
        deferred="Phase C1: short-lived OIDC federation for CI's cloud access",
        notes="There is no cloud deployment yet; the static test keeps cloud keys out meanwhile.",
    ),
    Control(
        "Container images are built from pinned base image digests",
        (f"{STATIC}::test_every_service_image_runs_as_non_root_with_a_healthcheck",),
        deferred="Phase C1: pinned base-image digests and cosign-signed images",
    ),
    # --- Data lifecycle ------------------------------------------------------------------------------
    Control(
        "Query result handles (Section 8.5) expire on a TTL",
        (
            f"{LIVE}::test_the_result_bucket_expires_objects",
            t(
                "dashboard-service",
                "integration/test_share_links.py::test_guest_tiles_without_data_when_expired_or_export_sized",
            ),
        ),
    ),
    Control(
        "Tenant deletion has a defined, tested cascade",
        (
            t(
                "identity-service",
                "integration/test_lifecycle.py::test_deleting_a_user_revokes_their_sessions_and_keys",
            ),
            t(
                "dashboard-service",
                "integration/test_share_links.py::test_deactivating_a_user_revokes_every_link_they_created",
            ),
        ),
        deferred="Phase C1: the tenant-deletion cascade across every owning service (Section 29)",
        notes="The user-level cascade (Section 6.7) is built and tested; tenant deletion is not.",
    ),
    Control(
        "PII columns are excluded from default agent context",
        (
            t(
                "metadata-service",
                "integration/test_internal_agent_context.py::test_context_hides_pii_hidden_tables_and_credentials",
            ),
            t(
                "query-gateway",
                "integration/test_queries.py::test_agent_path_runs_for_a_chat_user_and_excludes_pii",
            ),
            t(
                "query-gateway",
                "unit/test_sql_validator.py::test_agent_path_rejects_pii_and_invisible_tables",
            ),
        ),
        notes="Stricter than masking: the chat path never reads a PII column at all. Raw PII is "
        "reachable only on the SQL editor path, behind `sql:execute` plus a per-connection grant "
        "(A10), which is the platform's `pii:read`-equivalent.",
    ),
)
