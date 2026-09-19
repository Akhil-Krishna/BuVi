"""Outbound endpoint URLs (Section 15), checked at registration: MCP servers and webhooks.

Registration never touches the network: a hostname is judged on its resolved addresses at
request time (`platform_egress.pin_endpoint`). What *can* be refused from the text alone is:

* anything but `https` -- plain `http` only for an allow-listed internal host (dev, tests);
* credentials, a query string or a fragment in the URL (a token belongs in the secret store, and
  a URL that is stored and shown must not carry one);
* an IP literal that is not a public address, and numeric host spellings (`2130706433`,
  `0x7f.1`, `0177.0.0.1`) that resolvers expand into addresses the text hides;
* hosts that are not plain DNS names.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from platform_egress.policy import EgressPolicy

MAX_URL_LENGTH: Final = 2048
_LABEL: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NUMERIC_LABEL: Final = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
_PATH: Final = re.compile(r"^/[A-Za-z0-9\-._~/%!$&'()*+,;=:@]*$")


class EndpointRejected(ValueError):  # noqa: N818 - a policy verdict, raised with its reason
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Endpoint:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def url(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{host}{port}{self.path}"

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


def parse_endpoint(url: str, egress: EgressPolicy) -> Endpoint:
    """The normalized endpoint, or `EndpointRejected` with a fixed reason code."""
    if not url or len(url) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise EndpointRejected("malformed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise EndpointRejected("malformed") from None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise EndpointRejected("scheme_not_allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EndpointRejected("credentials_in_url")
    if parts.query or parts.fragment or url.rstrip().endswith(("?", "#")):
        raise EndpointRejected("query_or_fragment")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise EndpointRejected("malformed")
    host = _checked_host(host, egress)
    if scheme == "http" and not egress.is_allow_listed(host):
        raise EndpointRejected("https_required")
    path = parts.path or "/"
    if not _PATH.match(path) or "/../" in f"{path}/" or "/./" in f"{path}/":
        raise EndpointRejected("malformed")
    return Endpoint(scheme, host, port or (443 if scheme == "https" else 80), path)


def _checked_host(host: str, egress: EgressPolicy) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not egress.permits_literal(str(address)):
            raise EndpointRejected("destination_not_allowed")
        return str(address)
    labels = host.split(".")
    if all(_NUMERIC_LABEL.match(label) for label in labels):
        raise EndpointRejected("numeric_host")  # an IP in disguise, e.g. 2130706433 or 0x7f.1
    if len(host) > 253 or not all(_LABEL.match(label) for label in labels):
        raise EndpointRejected("invalid_host")
    return host
