"""analytics-orchestrator: conversations, runs, the CrewAI AnalyticsFlow (Sections 3, 10).

CrewAI reads its telemetry and tracing switches at import time, so they are forced off here --
before any module of this package can import CrewAI. Nothing about a run may leave the platform
through a framework's own telemetry (Sections 10.3, 29). ADR 0006.
"""

import os as _os

for _name, _value in (
    ("OTEL_SDK_DISABLED", "true"),
    ("CREWAI_DISABLE_TELEMETRY", "true"),
    ("CREWAI_DISABLE_TRACKING", "true"),
    ("CREWAI_TRACING_ENABLED", "false"),
    ("CREWAI_DISABLE_VERSION_CHECK", "true"),
):
    _os.environ[_name] = _value
