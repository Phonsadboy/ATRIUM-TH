"""ATRIUM — headless AI-company core.

A FastAPI service that runs a live AI company of Claude-backed department
agents under an executive, with per-department memory
(archive + RAG + knowledge graph), a durable job queue, autonomy with
guardrails, cost analytics, and the full v0.4 collaboration layer
(artifacts, projects, skills, decisions, war rooms, …).

It serves the exact UI contract over REST + WebSocket for the React/Phaser
client.
"""

__version__ = "0.4.0"
