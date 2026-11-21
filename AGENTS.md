# Working on tink-sdlc

- Subtract first; say each thing once, plainly.
- Fewer, deeper modules, smaller interfaces. Check data once where it comes in, then just use it.
  Write one-off logic right where it is used. Skip try/except wherever you can; shrink existing
  `try` blocks in `sdlc.py` only when you touch that code.
- Before a PR, run `python3 -m unittest discover -s tests` and `python3 scripts/package.py --check`.
