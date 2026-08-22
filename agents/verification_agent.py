"""Compatibility module for the required filename."""

from agents.verifier import ConflictType, DataLogicVerifierAgent, SUPPORTED_CONFLICT_TYPES

VerificationAgent = DataLogicVerifierAgent
__all__ = ["VerificationAgent", "DataLogicVerifierAgent", "ConflictType", "SUPPORTED_CONFLICT_TYPES"]
