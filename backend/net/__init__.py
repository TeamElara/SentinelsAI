"""Outbound network safety for connections to user-supplied hosts.

Everything that connects to a host a user typed (or that a scan discovered)
goes through `net.policy` first. Clients that only ever call fixed hosts
(GitHub, OSV, Docker Hub, Groq) don't need it.
"""
