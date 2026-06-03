"""Org graph — capabilities, routing, lifecycle (ATRIUM v2 Phase 3)."""
from .capabilities import (
    CapabilityRegistry,
    build_capability_registry,
    deactivate_department_capabilities,
    sync_all_department_capabilities,
    sync_department_capabilities,
)
from .checkpoints import create_org_checkpoint, rollback_org_checkpoint
from .lifecycle import enqueue_org_lifecycle, merge_departments, process_org_lifecycle, retire_department, spawn_department
from .router import resolve_handoff_target, route_task_to_department

__all__ = [
    "CapabilityRegistry",
    "build_capability_registry",
    "create_org_checkpoint",
    "deactivate_department_capabilities",
    "enqueue_org_lifecycle",
    "merge_departments",
    "process_org_lifecycle",
    "resolve_handoff_target",
    "retire_department",
    "rollback_org_checkpoint",
    "route_task_to_department",
    "spawn_department",
    "sync_all_department_capabilities",
    "sync_department_capabilities",
]
