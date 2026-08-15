"""C-1 contract errors."""


class AnalysisBriefError(ValueError):
    """Base validation failure for local analysis briefs."""


class DuplicateCanonicalEvidenceIdentityError(AnalysisBriefError):
    """An adapter emitted one semantic evidence identity more than once."""


class EvidenceHashCollisionError(AnalysisBriefError):
    """Different canonical identities produced the same short evidence hash."""


class SourceBundleMutationError(AnalysisBriefError):
    """A source bundle changed between load and final write."""
