"""Contract-first core for ChatBot 1C."""

from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill

__all__ = ["EvidenceBundle", "PlannerOutput", "Skill", "SkillPackage", "__version__"]
__version__ = "0.1.0-alpha.5"
