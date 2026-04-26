"""Tool handler implementations.

One module per tool. Each handler is a plain function that takes a
parsed input dict (already JSON-Schema-validated by `tools.validation`)
plus an optional `ctx` dict, and returns a JSON-encoded string. The
dispatch table in `tools.registry` wires handler name → function.
"""
