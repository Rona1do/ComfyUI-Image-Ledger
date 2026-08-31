# Contributing

Bug reports, compatibility findings, translations, documentation fixes, and focused pull requests are welcome.

## Before opening an issue

- Search existing issues and reproduce on a recent ComfyUI version.
- Back up source images before testing file-moving behavior.
- Do not attach private workflows, source images, databases, tokens, or unredacted logs.

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q .
node --check web/global_tracker.js
node --check web/image_ledger.js
```

## Pull requests

- Keep changes focused and explain user-visible behavior.
- Add or update tests for backend logic.
- Update both READMEs when installation or behavior changes.
- Preserve the non-destructive default: file moves must remain opt-in.
- Never weaken input/output path containment.
- Add an entry under `Unreleased` in `CHANGELOG.md`.

Contributions are licensed under the MIT License.
