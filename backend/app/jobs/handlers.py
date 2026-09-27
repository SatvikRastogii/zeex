"""Importing this module registers every job handler and inbound router."""

from app.agents import evaluation, negotiation, outreach, quote_intake, work_orders  # noqa: F401
from app.demo import autoreply  # noqa: F401
