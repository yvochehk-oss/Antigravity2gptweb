"""V0.2: AI 子包。"""

from ..constants import HEALTH_PROFILES, SCOPES
from .adapter import (
    AIEndpointTimeout,
    AIEndpointUnavailable,
    AIResponseContractError,
    call_endpoint,
    call_text_endpoint,
    endpoint_is_allowed,
    ensure_endpoint_allowed,
    is_mock_endpoint,
    mock_endpoint_allowed,
)
from .context import build_context
from .failover import (
    FailoverResult,
    TextFailoverResult,
    call_text_with_failover,
    call_with_failover,
    select_failover_endpoints,
)
from .mock import mock_review
from .orchestrator import (
    build_consensus,
    claim_health_batch,
    enqueue_health_check,
    fail_health_batch,
    health_endpoint_deadline_seconds,
    recheck_task,
    recover_stale_health_batches,
    run_health_check,
)
from .prompt import build_messages, get_prompt_template
from .review import now_iso, run_review

__all__ = [
    "SCOPES",
    "HEALTH_PROFILES",
    "build_context",
    "get_prompt_template",
    "build_messages",
    "call_endpoint",
    "call_text_endpoint",
    "call_with_failover",
    "call_text_with_failover",
    "select_failover_endpoints",
    "FailoverResult",
    "TextFailoverResult",
    "AIEndpointUnavailable",
    "AIEndpointTimeout",
    "AIResponseContractError",
    "endpoint_is_allowed",
    "ensure_endpoint_allowed",
    "is_mock_endpoint",
    "mock_endpoint_allowed",
    "mock_review",
    "run_review",
    "now_iso",
    "run_health_check",
    "build_consensus",
    "recheck_task",
    "claim_health_batch",
    "enqueue_health_check",
    "fail_health_batch",
    "recover_stale_health_batches",
    "health_endpoint_deadline_seconds",
]
