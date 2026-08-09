"""Agent registry — the single list of all scanner agents.

Adding a new agent = write the class + add one line here. No other file
(routes, orchestrator, frontend) needs to change to discover the new agent.
"""
from __future__ import annotations

from agents.api_security import ApiSecurityAgent
from agents.dns_email import DNSAgent
from agents.exposure import ExposureAgent
from agents.headers import HeadersAgent
from agents.misconfig import MisconfigAgent
from agents.recon import ReconAgent
from agents.subdomain import SubdomainAgent
from agents.tls import TLSAgent
from models import AgentInfo

AGENTS = [
    HeadersAgent, ReconAgent, TLSAgent, ExposureAgent, DNSAgent,
    ApiSecurityAgent, MisconfigAgent, SubdomainAgent,
]


def list_agents() -> list[AgentInfo]:
    """Return metadata for every registered agent, in registration order."""
    return [
        AgentInfo(
            name=cls.name,
            display_name=cls.display_name,
            purpose=cls.purpose,
            checks=cls.checks,
            category=cls.category,
        )
        for cls in AGENTS
    ]
