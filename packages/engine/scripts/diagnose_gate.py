#!/usr/bin/env python3
"""Find CLIApprovalGate's actual API."""
import sys
sys.path.insert(0, ".")

from src.core.approval_gate import CLIApprovalGate
import inspect

print("CLIApprovalGate methods:")
for name in sorted(dir(CLIApprovalGate)):
    if not name.startswith('_'):
        attr = getattr(CLIApprovalGate, name)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
                print(f"  {name}{sig}")
            except (ValueError, TypeError):
                print(f"  {name}(...)")

# Also check parent classes
print(f"\nMRO: {[c.__name__ for c in CLIApprovalGate.__mro__]}")

# Check for the specific method we need
for method_name in ['present', 'submit', 'review', 'present_proposal', 'approve', 'run']:
    has = hasattr(CLIApprovalGate, method_name)
    print(f"  has '{method_name}': {has}")
