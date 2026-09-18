"""Rate-limit policy (Sections 5, 20, 24): which token buckets guard a request.

Pure data and key-building. The atomic token-bucket arithmetic runs in Redis
(`infrastructure/cache/rate_limiter.py`) so every gateway replica shares one count.

Tiers, all enforced together on a request:

* per IP, before authentication, so a flood costs no session introspection -- a strict `auth`
  bucket for login, callback, invitation acceptance and MFA verification (Section 24: auth
  endpoints get limits independent of the general limiter), a `public` bucket for the other
  public routes, and a flood guard for authenticated routes. That guard is sized for a whole
  tenant behind one NAT or proxy address: fair use per person is the user bucket's job, not
  the IP's;
* per user and per tenant, after authentication -- so one noisy user cannot exhaust
  their tenant, and one noisy tenant cannot starve the platform (Section 20).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal["ip", "user", "tenant"]


@dataclass(frozen=True)
class BucketRule:
    name: str
    scope: Scope
    capacity: int
    refill_per_second: float


@dataclass(frozen=True)
class Bucket:
    rule: BucketRule
    key: str


@dataclass(frozen=True)
class RateLimitPolicy:
    auth_ip: BucketRule
    public_ip: BucketRule
    authenticated_ip: BucketRule
    user: BucketRule
    tenant: BucketRule
    key_prefix: str = "rl"

    def before_auth(self, *, rate_tier: str, client_ip: str) -> list[Bucket]:
        rule = {"auth": self.auth_ip, "public": self.public_ip}.get(
            rate_tier, self.authenticated_ip
        )
        return [Bucket(rule, f"{self.key_prefix}:{rule.name}:ip:{client_ip}")]

    def after_auth(self, *, tenant_id: str, user_id: str) -> list[Bucket]:
        return [
            Bucket(self.user, f"{self.key_prefix}:{self.user.name}:{tenant_id}:{user_id}"),
            Bucket(self.tenant, f"{self.key_prefix}:{self.tenant.name}:{tenant_id}"),
        ]
