# SmartSake — Project Instructions

## README Maintenance

**Whenever any code, config, wiring, or feature change is made in this repo, update `README.md` to reflect it.** This includes but is not limited to:

- New or changed GPIO pin assignments
- New or modified config file fields/schemas
- New HTML pages or removed pages
- Changed polling intervals or timing constants
- New scripts or CLI commands
- Changed systemd unit behavior
- New dependencies (pip, apt, hardware)
- Changed file structure

The README is the single source of truth for setup, operations, and usage. If the code changes and the README doesn't match, the README is wrong.

## Branch Rules

- Active branch: `ClaudeAgents`
- `main` and `zany` are protected — do not push to them

## Coding Standards

- Generation delegated to non-Anthropic models; Claude plans and reviews
- All hardware imports must degrade gracefully (try/except with warning, no crash)
- Relay logic is active-LOW (GPIO LOW = fan ON)
- Config files are hot-reloaded by mtime check — no restart needed for config changes
