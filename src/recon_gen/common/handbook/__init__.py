"""Handbook templating support — vocabulary + diagram render hooks.

Public surface (BXa.1 trimmed):
- ``HandbookVocabulary`` + ``InstitutionVocabulary``
- ``vocabulary_for(l2_instance)`` — single dispatch (no built-in
  vocabularies post-BXa.1; institution name + acronym +
  investigation_personas come from the L2 YAML directly).

Removed in BXa.1 (doubly-dead per `docs/audits/bx_persona_audit.md`):
- ``StakeholderVocabulary`` + ``MerchantVocabulary`` — hardcoded
  tables that were never substituted in any docs page.
- ``InvestigationPersonaVocabulary`` — replaced by the typed
  ``InvestigationPersona`` on ``L2Instance.investigation_personas``.
"""

from __future__ import annotations

from .vocabulary import (
    HandbookVocabulary,
    InstitutionVocabulary,
    vocabulary_for,
    vocabulary_for_l2,
)

__all__ = [
    "HandbookVocabulary",
    "InstitutionVocabulary",
    "vocabulary_for",
    "vocabulary_for_l2",
]
