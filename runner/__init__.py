"""parallax-engine autonomous build harness.

Modules:
  budget        — dollar/wallclock cap tracking and persistence
  client        — Claude Agent SDK client factory
  integrity     — post-session milestone schema verification
  progress      — phase_milestones.json read-only summarization
  prompts       — load initializer/coding prompts from disk
  security      — bash command allowlist and forbidden-package blocking
  agent_session — per-session async loop with budget tracking
  run_parallax_build — main entry point
"""
