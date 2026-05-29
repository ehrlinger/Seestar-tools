## Summary

<!-- 1-3 sentences: what changed and why. Link any related issue. -->

## Testing

<!-- How was this verified? Tick what applies, add detail where useful. -->

- [ ] `python -m unittest discover -s tests -v` passes locally
- [ ] CI green on all matrix cells (macOS / Ubuntu / Windows × Python 3.10 / 3.12 / 3.13)
- [ ] Manually exercised on real Seestar data (describe target / paths)
- [ ] N/A — docs or workflow-only change

## Risk / rollback

<!-- Optional. What could go wrong? How would you back this out?
     For data-touching changes (cleanup_seestar.py --merge, batch_stack.py),
     note whether a --dry-run was run against real data first. -->
