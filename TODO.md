# agent(-1) TODO

## Agent home directory

- [ ] Choose a configurable data directory under the user's home, such as
  `~/.agent-1/`.
- [ ] Move sessions, `MEMORY.md`, `USER.md`, and other agent-owned runtime files
  there. Scratchpads remain part of their session JSON.
- [ ] Define global user preferences versus project-specific memory and sessions.
- [ ] Move existing data without overwriting notes or losing session history.
- [ ] Keep workspace paths explicit when running from another directory.

## File tools

- [ ] Make writes and edits atomic and preserve encoding and line endings.
- [ ] Detect stale file content before applying an edit.
- [ ] Bound traversal time, files visited, and output; support cancellation.
- [ ] Respect ignore rules and exclude generated directories by default.
- [ ] Report skipped files, truncation, and partial results clearly.
- [ ] Evaluate ast-grep for structural search and rewrite previews.
- [ ] Evaluate difftastic for inspecting changes; keep patch application separate.
- [ ] Add regression coverage for workspace boundaries and interrupted edits.

## Safe shell execution

- [ ] Support PowerShell and Bash through explicit, configurable executables.
  Account for native Bash, Git Bash, and WSL where appropriate.
- [ ] Define the execution policy: permitted actions, approval boundaries,
  filesystem access, and network access.
- [ ] Choose OS-enforced isolation. A working directory or command blocklist
  alone does not confine a shell.
- [ ] Pass a minimal environment; keep model credentials and unrelated secrets
  outside the command's environment and accessible filesystem.
- [ ] Enforce timeouts, output limits, cancellation, and child-process cleanup.
- [ ] Return structured stdout, stderr, exit status, and timeout information.
- [ ] Record executed commands and outcomes without unnecessarily logging secrets.
- [ ] Verify allowed and denied operations using local fixtures and dummy
  credentials, without live model calls.
