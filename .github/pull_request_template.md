## Summary

<!-- What does this PR change? Why? -->

## Related issue

<!-- Fixes #NNN, or "none". Non-trivial PRs should link an issue - see CONTRIBUTING.md -->

## Test plan

- [ ] `uvicorn watchtower.main:app --reload` starts without traceback
- [ ] `npm run build` completes in `frontend/`
- [ ] `az bicep build --file infra/main.bicep` completes without warnings (if infra changed)
- [ ] Manual smoke: describe what you clicked / called

## Notes for reviewers

<!-- Anything non-obvious. Screenshots for UI changes. -->
