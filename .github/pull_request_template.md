## What this changes

## Why

## How it was verified

- [ ] `python3 -m unittest discover -s tests -t .` (paste the final line)
- [ ] New tests use `tests.support.Sandbox`; nothing reaches a real app
- [ ] External programs run only through `corelib.effects`
- [ ] Nothing in `build/` is committed
- [ ] Built-in themes look the same, or the change says why not
- [ ] No third-party Python dependency was added
- [ ] Docs updated where behaviour changed

## Anything deliberately not done
