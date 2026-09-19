# juniq

Drop lines that mean the same as an earlier line. **Primitive: noul over pairs.**

```console
$ cat feature-requests.txt | juniq "same underlying request" -c | sort -rn
      3 Please add dark mode to the app
      2 Export data as CSV
      1 Fix the login crash

$ sort bugs.txt | juniq "duplicate bug report" --show-groups
App crashes on launch
  ↳ Crash when opening the app
  ↳ Startup crash on iOS
```

Like `uniq`, but "the same" is a judgment. The first occurrence is kept.

## Usage

```
juniq [options] [DESCRIPTION] [FILE ...]
```

The description is optional (default: *the same thing said in different words*). A first
argument that names an existing file is treated as a file.

| option | meaning |
|---|---|
| `-p P` | probability at which two lines count as duplicates (default 0.5) |
| `-w`, `--window N` | compare each line with the previous N **distinct** lines (exact repeats cost nothing and take no room in the window; default 50, max 200) |
| `-c`, `--count` | prefix each kept line with the size of its group |
| `-d`, `--repeated` | print only lines that had duplicates |
| `-u`, `--unique` | print only lines that had none |
| `--show-groups` | print each kept line followed by its duplicates, indented |
| `--json` | `{"line", "count", "duplicates": [...], "source", "lineno"}` per kept line |

## How it works

Exact repeats (ignoring case and spacing) are dropped for free. Every other line is compared, in
**one call**, against the previous `--window` distinct lines: the state is
`{"candidate": line, "kept": [...]}` with one yes/no question per earlier line. Jev answers all
the questions in parallel, so a 50-line window costs tokens, not time.

All lines are judged concurrently; verdicts are then resolved in input order, so a line that is
itself a duplicate still pulls later lines into its group (dup-of-a-dup is a dup of the original).

## Behaviour

- Fail-open: a line that could not be compared is **kept** (data is never dropped on an error)
  and the exit status becomes 5.
- Exit status: 0, 1 when nothing is printed (no input, or no line survived `-d`/`-u`, so
  `juniq -d … && alert` does not fire when there are no duplicates), 2 usage or an unreadable
  file, 3 auth, 4 API, 5 partial.

## Cost

One call per unique line with up to `--window` questions. With a 50-line window and 100-character
lines, about 1,500 tokens per line: 1,000 lines cost about $0.06 at list price.
