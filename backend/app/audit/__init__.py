"""Tamper-evident hash-chained audit logging for Break-Glass Voice Gate."""

from .service import list_events, record_event, verify_chain

__all__ = ["record_event", "list_events", "verify_chain"]
