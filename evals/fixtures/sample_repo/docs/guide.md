# Adding a custom tool

Subclass `Tool`, declare a JSON schema, and pass an instance to
`Agent(tools=[...])`. Each agent owns its own registry, so no global
registration is required.

## Permission metadata

Declare `risk` and `parallel_safe` so the executor can apply policy and
concurrency rules before any Python code runs.
