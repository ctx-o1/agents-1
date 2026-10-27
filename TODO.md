# agent(-1) TODO

## Agent home directory

- [x] Choose a configurable data directory (`AGENT_1_DATA_DIR`); default to
  `./.agent-1/` for debugging, with `~/.agent-1/` available as an override.
- [x] Store sessions, `MEMORY.md`, `USER.md`, and other agent-owned runtime files
  there. Scratchpads remain part of their session JSON.
- [ ] Define global user preferences versus project-specific memory and sessions.
- [ ] Keep workspace paths explicit when running from another directory.
- [ ] Allow agent to run in current directory. No `workspace`.
- [ ] Improve file system tools.
- [ ] Support PowerShell and Bash through explicit, configurable executables.
- [ ] Define the execution policy: permitted actions, approval boundaries,
  filesystem access, and network access.
- [ ] Choose OS-enforced isolation. A working directory or command blocklist
  alone does not confine a shell.
- [ ] Pass a minimal environment; keep model credentials and unrelated secrets
  outside the command's environment and accessible filesystem.
- [ ] Enforce timeouts, output limits, cancellation, and child-process cleanup.

