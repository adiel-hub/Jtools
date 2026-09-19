# jgate

A conditional pipe barrier: exit 0 if the input fits, 1 if not. **Primitive: noul.**

```console
$ git diff | jgate "this change is safe to auto-merge" && git merge
$ cat report.txt | jgate "mentions a security incident" && alert-oncall
$ tail -50 app.log | jgate --each "a request took longer than a second" && page-me
$ cat draft.md | jgate -P "ready to publish" | publish   # -P copies the input through on success
```

## Usage

```
jgate [options] DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `-p P` | probability needed to pass (default 0.5) |
| `--each` | judge each line; pass if **any** line fits |
| `--all` | judge each line; pass only if **all** non-blank lines fit |
| `-P`, `--print` | on success, copy the input to stdout so the gate can sit inside a pipe |
| `--fail-open` | pass (exit 0) when Jev cannot be reached, instead of exit 4 |
| `--json` | print `{"pass", "probability", "threshold"}` (per-line modes add counts) |
| `-v` | print the probability and the decision to stderr |
| `--jsonl`, `--csv` | read [records, not lines](../structured.md): each record is judged whole (needs `--each`/`--all`); `-P` copies the header through |
| `--field NAME` | with `--jsonl`/`--csv`: judge only this value (dotted JSON paths work), still printing the whole record |

## Fails closed

Every other j-tool fails open: an unjudged line passes through. A gate does the opposite. If the
API is down, the key is wrong or the budget is spent, jgate exits 4 and the `&&` chain stops,
because a gate that lets `git merge` run during an outage is not a gate. `--fail-open` restores
pass-through behaviour when that is what you want (`--strict` is the default here and cannot be
combined with `--fail-open`).

## Behaviour

- Whole-input mode sends the entire input as one state (first 60,000 characters per file by
  default; `--max-chars` raises it). Empty input exits 1.
- `--each` stops at the first fitting line; `--all` stops at the first line that does not fit.
  A line that could not be judged fails the gate (exit 4) whenever it could change the verdict:
  always under `--all`, and under `--each` when no judged line fit. `--fail-open` turns that into
  a pass. With `-P` the input is read first, then echoed complete and in input order.
- Whole-input mode defaults to 60,000 characters; per-line modes keep the common 8,000. A record
  cut at that limit was judged on a prefix, and the run says so.
- **A file that could not be read is exit 2, never a pass.** A gate cannot assert anything about
  input it never opened, so `jgate --all "safe to ship" *.log && deploy` refuses rather than
  deploying because one of the files was missing.
- `--strict` fails closed on the first API error in every mode, whole input and per line alike.
- The verdict survives losing stdout: `jgate --json … | head` keeps its exit status, where every
  other tool treats a closed pipe as "the reader has what it wanted" and exits 0.
- Exit status: 0 pass, 1 fail, 2 usage or unreadable input, 3 auth, 4 API or unjudged.

## Not a security boundary

Text in the input can try to steer the answer. Use `-p 0.9` and a human for the things that
matter; use jgate to save the human's time on the rest.
