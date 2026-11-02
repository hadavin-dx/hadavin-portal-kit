## What and why

## Checks (tick only what you ran; paste the command)
- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run pytest` (offline; a socket opening fails the test)
- [ ] Export validates against `schema/demo-export.v1.json`
- [ ] Single-dependency audit: any vendor SDK sits behind a swappable kind with a neutral default
- [ ] Synthetic data only; no secrets, real brands or personal data
- [ ] What is faked is stated (scripted model, fake adapters)

## What I could not verify
