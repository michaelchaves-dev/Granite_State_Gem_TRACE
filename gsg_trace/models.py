"""Core schemas for GSG TRACE.

Every type here is deliberately plain (dataclasses + str enums) so it serializes
to JSON without custom encoders and can be mirrored 1:1 in a database schema or
a TypeScript client later.
"""
from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "0.1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Fixed precision keeps ISO strings lexicographically sortable (used in SQL comparisons).
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_json(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_id() -> str:
    return str(uuid.uuid4())


class IdentifierType(str, enum.Enum):
    BUSINESS_NAME = "business_name"
    PHONE = "phone"
    DOMAIN = "domain"
    EMAIL = "email"
    ADDRESS = "address"
    SOCIAL_PROFILE = "social_profile"
    REGISTRY_ID = "registry_id"


class SourceTier(enum.IntEnum):
    OFFICIAL = 1               # government / authoritative records
    BUSINESS_CONTROLLED = 2    # official business sites, published contact info
    DIRECTORY_REVIEW = 3       # independent directories, review platforms
    HISTORICAL = 4             # archives, historical listings


class SourceType(str, enum.Enum):
    OFFICIAL_REGISTRY = "official_registry"
    LICENSING = "licensing"
    PERMIT = "permit"
    COURT = "court"
    BUSINESS_SITE = "business_site"
    DIRECTORY = "directory"
    REVIEW = "review"
    ARCHIVE = "archive"


class RelationshipStatus(str, enum.Enum):
    VERIFIED_LEGAL = "verified_legal"
    DOCUMENTED_OPERATIONAL = "documented_operational"
    UNRESOLVED = "unresolved"
    CONTRADICTED = "contradicted"
    UNRELATED = "unrelated"
    INSUFFICIENT = "insufficient_information"


class VerificationStatus(str, enum.Enum):
    VERIFIED = "verified"                # read directly from an authoritative record for the matched entity
    SOURCE_REPORTED = "source_reported"  # stated by a direct/secondary source; not independently verified
    ALLEGATION = "allegation"            # user-generated account (review / complaint)
    UNVERIFIED = "unverified"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class LinkPolarity(str, enum.Enum):
    ASSERTS = "asserts"
    DENIES = "denies"


# Link types a Tier-1 record can establish as a *legal* relationship.
LEGAL_LINK_TYPES = frozenset(
    {"successor_of", "predecessor_of", "merged_into", "merged_from", "converted_from", "converted_to", "parent_of", "subsidiary_of"}
)
# Link types that describe an operational (not necessarily legal) connection.
OPERATIONAL_LINK_TYPES = frozenset({"operates_as", "division_of", "affiliate_of", "franchise_of", "contracted_by"})


@dataclass(frozen=True)
class Identifier:
    type: IdentifierType
    normalized: str
    originals: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.type.value}:{self.normalized}"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "normalized": self.normalized, "originals": list(self.originals)}


@dataclass
class SourceRecord:
    """One record returned by a source adapter. Content is untrusted data."""

    source_id: str
    source_name: str
    source_type: SourceType
    tier: SourceTier
    record_id: str
    url: str
    retrieved_at: datetime
    entity_id: str | None
    fields: dict[str, Any]
    excerpt: str
    content_hash: str
    event_date: str | None = None
    jurisdiction: str | None = None
    origin_key: str | None = None   # identifies the underlying claim origin, for corroboration counting
    synthetic: bool = False

    @property
    def entity_key(self) -> str | None:
        return f"{self.source_id}:{self.entity_id}" if self.entity_id else None

    @property
    def record_key(self) -> str:
        return f"{self.source_id}:{self.record_id}"

    @property
    def origin(self) -> str:
        return self.origin_key or self.record_key

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_type"] = self.source_type.value
        d["tier"] = int(self.tier)
        d["retrieved_at"] = iso(self.retrieved_at)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceRecord":
        d = dict(d)
        d["source_type"] = SourceType(d["source_type"])
        d["tier"] = SourceTier(d["tier"])
        d["retrieved_at"] = parse_iso(d["retrieved_at"])
        return cls(**d)


@dataclass(frozen=True)
class LinkAssertion:
    """A source's statement that two entities are (or are not) linked."""

    from_entity: str
    to_entity: str
    link_type: str
    polarity: LinkPolarity
    record_key: str
    origin: str
    tier: SourceTier
    source_name: str


@dataclass
class Relationship:
    subject_entity: str
    related_entity: str
    status: RelationshipStatus
    basis: str
    link_types: list[str] = field(default_factory=list)
    supporting_records: list[str] = field(default_factory=list)
    contradicting_records: list[str] = field(default_factory=list)
    shared_identifiers: list[str] = field(default_factory=list)
    independent_origins: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class EvidenceReceipt:
    claim_id: str
    subject_business_id: str
    claim_text: str
    claim_category: str
    source_type: str
    source_name: str
    source_url_or_reference: str
    source_record_identifier: str
    observed_at_utc: str
    source_event_date: str | None
    relevant_excerpt_or_locator: str
    jurisdiction: str | None
    search_scope: str
    relationship_status: str | None
    verification_status: str
    limitations: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)
    review_status: str = ReviewStatus.PENDING.value
    correction_history: list[dict[str, Any]] = field(default_factory=list)
    content_hash: str | None = None
    synthetic_source: bool = False

    REQUIRED_CITATION_FIELDS = (
        "claim_text",
        "source_type",
        "source_name",
        "source_url_or_reference",
        "source_record_identifier",
        "observed_at_utc",
        "relevant_excerpt_or_locator",
    )

    def missing_citation_fields(self) -> list[str]:
        return [f for f in self.REQUIRED_CITATION_FIELDS if not str(getattr(self, f) or "").strip()]

    @property
    def citation_complete(self) -> bool:
        return not self.missing_citation_fields()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchLimits:
    max_depth: int = 2
    max_branches_per_node: int = 6
    max_requests: int = 25
    max_seconds: float = 30.0
    max_cost_usd: float = 0.0     # default paid-search budget is $0 (spec §4)
    min_relevance: float = 0.5
    max_entities: int = 50

    def validate(self) -> None:
        if not (0 <= self.max_depth <= 5):
            raise ValueError("max_depth must be between 0 and 5")
        if self.max_requests < 1 or self.max_requests > 500:
            raise ValueError("max_requests must be between 1 and 500")
        if self.max_seconds <= 0 or self.max_seconds > 600:
            raise ValueError("max_seconds must be in (0, 600]")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd cannot be negative")
        if not (0.0 <= self.min_relevance <= 1.0):
            raise ValueError("min_relevance must be within [0, 1]")
        if self.max_branches_per_node < 1 or self.max_entities < 1:
            raise ValueError("branch and entity limits must be >= 1")


@dataclass
class SearchLogEntry:
    step: int
    identifier_key: str          # kept only in private run records / reports
    identifier_key_hash: str     # the only form emitted to application logs
    identifier_type: str
    source_id: str
    depth: int
    reason: str
    outcome: str
    records_returned: int = 0
    cost_usd: float = 0.0
    at: str | None = None
