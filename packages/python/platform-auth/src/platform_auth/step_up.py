"""The one refusal every service and the gateway give for a missing step-up (Section 7.3).

`403 STEP_UP_REQUIRED` with `WWW-Authenticate: MFA realm="step-up", max_age=300`, and
`details.method` = `webauthn` when only a WebAuthn verification will do (Section 6.6), else
`any`. Clients use it to choose which factor to prompt for.
"""

from __future__ import annotations

from platform_auth.principal import STEP_UP_MAX_AGE, Principal
from platform_observability.errors import ApiError


class StepUpRequiredError(ApiError):
    code = "STEP_UP_REQUIRED"
    status_code = 403
    message = "This operation requires re-verifying your identity."

    @classmethod
    def for_principal(cls, principal: Principal) -> StepUpRequiredError:
        max_age = int(STEP_UP_MAX_AGE.total_seconds())
        return cls(
            headers={"WWW-Authenticate": f'MFA realm="step-up", max_age={max_age}'},
            method=principal.step_up_method or "any",
        )
