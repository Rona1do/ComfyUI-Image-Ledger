# Security Policy

Security fixes are provided for the latest released version.

Do not open a public issue for a vulnerability that could expose or modify files outside ComfyUI's data directories. Use GitHub private vulnerability reporting when it is available; otherwise contact the maintainer through the GitHub profile without disclosing details publicly. Include the affected version, platform, reproduction steps, and impact, but no private images, workflows, tokens, or unredacted paths.

We aim to acknowledge reports within 7 days and provide an initial assessment within 14 days.

## Security boundaries

- API paths must remain relative to ComfyUI input/output roots.
- Directory links deliberately placed under those roots are supported.
- Source-file movement is opt-in and should be tested on backed-up data.
- The project has no telemetry and makes no outbound network requests.
