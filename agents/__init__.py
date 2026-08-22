"""Five specialized research-writing agents."""

from agents.editor import EditorialAgent
from agents.experiment import ExperimentPlanningAgent
from agents.method import MethodDesignAgent
from agents.research import LiteratureResearchAgent
from agents.verifier import DataLogicVerifierAgent

__all__ = [
    "LiteratureResearchAgent",
    "MethodDesignAgent",
    "ExperimentPlanningAgent",
    "DataLogicVerifierAgent",
    "EditorialAgent",
]

