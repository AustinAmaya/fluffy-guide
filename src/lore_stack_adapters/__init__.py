"""Live model adapters for the lore-stack seams.

This package sits OUTSIDE the lore_stack core: the core never imports it, and
the dependency arrow points one way (adapter -> core). The fakes in
lore_stack.seams remain the default everywhere; these adapters are opt-in.
"""
