"""Hermes Helpdesk: fictional tickets, notes, fake mail, and fake refunds, scoped to
one visitor session and never sending or moving anything for real."""

from __future__ import annotations

from petasos.helpdesk.data import HELPDESK_SCHEMA, seed_session
from petasos.helpdesk.tools import ReadCapture, build_registry

__all__ = ["HELPDESK_SCHEMA", "ReadCapture", "build_registry", "seed_session"]
