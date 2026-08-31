# Release process

1. Confirm the working tree is clean and CI is green.
2. Test global tracking with move-on-success both disabled and enabled on disposable files.
3. Test the Visual Queue → Commit Finished Video path in a current ComfyUI install.
4. Update both READMEs, `CHANGELOG.md`, and the version in `pyproject.toml`.
5. Commit with `release: vX.Y.Z` and create an annotated `vX.Y.Z` tag.
6. Create a GitHub release using the changelog section and attach no private runtime data.
7. After obtaining a ComfyUI Registry publisher ID and API key, add the required `[tool.comfy]` metadata and a separate registry publish workflow.
8. Monitor issues after release and document compatibility findings.
