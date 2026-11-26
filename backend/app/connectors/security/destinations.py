"""Destination validation: the server-side request forgery (SSRF) boundary.

A connector's URL is configured by an administrator, but it still decides where
the backend sends requests, so it is treated as untrusted input:

1. **Static checks** (``DestinationPolicy.check_url``), on every save and before
   every request: ``https`` only (``http`` only when explicitly enabled in
   development or test), no user info, no fragment, a real host name or IP
   literal, a host on the administrator-approved allow-list
   (``CONNECTOR_ALLOWED_HOSTS``), and IP literals that pass the address rules.
2. **Resolution checks** (``DestinationPolicy.resolve``), immediately before
   connecting: the name is resolved and **every** returned address must pass the
   address rules. The request is then sent to that validated address (the HTTP
   client pins the connection to it, keeping the host name for the ``Host``
   header and TLS), so a DNS answer that changes between the check and the
   connection (DNS rebinding) cannot redirect it.

Address rules: public (globally routable) unicast addresses are allowed.
Loopback, private, shared, link-local, multicast, reserved, unspecified and
other special-purpose ranges are refused unless the address falls inside a
network listed in ``CONNECTOR_ALLOWED_PRIVATE_NETWORKS``. Link-local (which
includes cloud metadata services such as 169.254.169.254), multicast,
broadcast and unspecified addresses are refused even then, and loopback is
refused outside development and test whatever the configuration says.

Redirects are never followed (see ``app.connectors.adapters.http_client``).
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import SplitResult, urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# A resolver returns the addresses for (host, port). Injected in tests.
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_PATTERN_RE = re.compile(rf"^(?:\*\.)?(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")

_NEVER_ALLOWED = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, cloud metadata services
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 metadata service
)

RejectionReason = Literal[
    "invalid_url",
    "scheme_not_allowed",
    "host_not_allowed",
    "address_not_allowed",
    "dns_resolution_failed",
]


class DestinationError(Exception):
    """The destination is not permitted. ``message`` is safe to show; it names no address."""

    def __init__(self, reason: RejectionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def normalize_host_pattern(value: str) -> str:
    """Validate one CONNECTOR_ALLOWED_HOSTS entry: a host name, ``*.domain`` or an IP literal."""
    text = value.strip().lower().rstrip(".")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    try:
        text = text.encode("idna").decode("ascii") if not text.startswith("*.") else "*." + text[2:].encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"CONNECTOR_ALLOWED_HOSTS entry is not a valid host name: {value!r}") from None
    if not _PATTERN_RE.match(text) or text.count("*") > 1:
        raise ValueError(f"CONNECTOR_ALLOWED_HOSTS entry is not a valid host name or *.domain pattern: {value!r}")
    if text.startswith("*.") and "." not in text[2:]:
        raise ValueError(f"CONNECTOR_ALLOWED_HOSTS wildcard must name a domain, not a top-level domain: {value!r}")
    return text


def _as_ip(host: str) -> IPAddress | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    scheme: str
    host: str  # lower case, IDNA-encoded; IPv6 without brackets
    port: int
    path: str
    ip_literal: IPAddress | None

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if isinstance(self.ip_literal, ipaddress.IPv6Address) else self.host
        return host if self.port == default else f"{host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host_header}"


@dataclass(frozen=True, slots=True)
class ResolvedDestination:
    url: ValidatedURL
    address: IPAddress


async def system_resolver(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


@dataclass
class DestinationPolicy:
    allowed_hosts: Sequence[str] = ()
    allowed_networks: Sequence[str] = ()
    allow_http: bool = False
    allow_loopback: bool = False
    resolver: Resolver = system_resolver
    _networks: list[IPNetwork] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.allowed_hosts = [normalize_host_pattern(h) for h in self.allowed_hosts]
        self._networks = [ipaddress.ip_network(n, strict=False) for n in self.allowed_networks]

    @classmethod
    def from_settings(cls, settings: object, resolver: Resolver | None = None) -> "DestinationPolicy":
        return cls(
            allowed_hosts=settings.connector_allowed_hosts,  # type: ignore[attr-defined]
            allowed_networks=settings.connector_allowed_private_networks,  # type: ignore[attr-defined]
            allow_http=settings.connector_http_allowed,  # type: ignore[attr-defined]
            allow_loopback=settings.connector_loopback_allowed,  # type: ignore[attr-defined]
            resolver=resolver or system_resolver,
        )

    # --- static checks --------------------------------------------------------------------------------------

    def parse(self, url: str) -> ValidatedURL:
        """Structure and scheme only (no allow-list): used to validate input before saving."""
        if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
            raise DestinationError("invalid_url", "The URL is not valid.")
        try:
            parts: SplitResult = urlsplit(url)
            port = parts.port
        except ValueError:
            raise DestinationError("invalid_url", "The URL is not valid.") from None
        scheme = parts.scheme.lower()
        if scheme not in ("https", "http"):
            raise DestinationError("scheme_not_allowed", "The URL must start with https://.")
        if scheme == "http" and not self.allow_http:
            raise DestinationError(
                "scheme_not_allowed",
                "Only https:// URLs are allowed. Plain http:// is available only in development with CONNECTOR_ALLOW_HTTP=true.",
            )
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise DestinationError("invalid_url", "The URL must not contain a user name or password. Configure authentication instead.")
        if parts.query or parts.fragment or url.rstrip().endswith(("?", "#")):
            raise DestinationError("invalid_url", "The URL must not contain a query string or fragment.")
        host = (parts.hostname or "").rstrip(".")
        if not host:
            raise DestinationError("invalid_url", "The URL must include a host name.")
        ip = _as_ip(host)
        if ip is None:
            try:
                host = host.encode("idna").decode("ascii").lower()
            except UnicodeError:
                raise DestinationError("invalid_url", "The URL's host name is not valid.") from None
            if not _HOSTNAME_RE.match(host):
                raise DestinationError("invalid_url", "The URL's host name is not valid.")
        else:
            host = str(ip)
        return ValidatedURL(
            scheme=scheme, host=host, port=port or (443 if scheme == "https" else 80), path=parts.path or "", ip_literal=ip
        )

    def host_allowed(self, host: str) -> bool:
        for pattern in self.allowed_hosts:
            if pattern.startswith("*."):
                suffix = pattern[1:]
                if host.endswith(suffix) and len(host) > len(suffix):
                    return True
            elif host == pattern:
                return True
        return False

    def address_allowed(self, address: IPAddress) -> bool:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if any(address in network for network in _NEVER_ALLOWED if network.version == address.version):
            return False
        if address.is_loopback and not self.allow_loopback:
            return False
        if address.is_global and not address.is_multicast:
            return True
        return any(address in network for network in self._networks if network.version == address.version)

    def check_url(self, url: str) -> ValidatedURL:
        """Every check that needs no network access."""
        parsed = self.parse(url)
        if parsed.host == "localhost" or parsed.host.endswith(".localhost"):
            if not self.allow_loopback:
                raise DestinationError("address_not_allowed", "Connections to localhost are not allowed.")
        if not self.host_allowed(parsed.host):
            raise DestinationError(
                "host_not_allowed",
                "This host is not on the server's list of approved connector hosts (CONNECTOR_ALLOWED_HOSTS).",
            )
        if parsed.ip_literal is not None and not self.address_allowed(parsed.ip_literal):
            raise DestinationError("address_not_allowed", _ADDRESS_MESSAGE)
        return parsed

    # --- resolution ------------------------------------------------------------------------------------------

    async def resolve(self, url: str) -> ResolvedDestination:
        """Static checks, then DNS: every address the name resolves to must be permitted."""
        parsed = self.check_url(url)
        if parsed.ip_literal is not None:
            return ResolvedDestination(parsed, parsed.ip_literal)
        try:
            answers = await self.resolver(parsed.host, parsed.port)
        except (OSError, UnicodeError):
            raise DestinationError("dns_resolution_failed", "The connector's host name could not be resolved.") from None
        addresses: list[IPAddress] = []
        for answer in answers:
            address = _as_ip(str(answer).split("%", 1)[0])
            if address is None:
                raise DestinationError("dns_resolution_failed", "The connector's host name could not be resolved.")
            addresses.append(address)
        if not addresses:
            raise DestinationError("dns_resolution_failed", "The connector's host name could not be resolved.")
        # One bad answer rejects the whole destination: an attacker controlling DNS could otherwise
        # mix a public address (passes) with an internal one (gets connected to).
        if not all(self.address_allowed(a) for a in addresses):
            raise DestinationError("address_not_allowed", _ADDRESS_MESSAGE)
        return ResolvedDestination(parsed, addresses[0])


_ADDRESS_MESSAGE = (
    "The connector's host resolves to a private, loopback or reserved network address, which is not allowed. "
    "If the company backend is on a private network, the server administrator must approve that network "
    "in CONNECTOR_ALLOWED_PRIVATE_NETWORKS."
)
