# jmatch

A semantic `join`: for each line in FILE_A, the best-matching line in FILE_B. **Primitive: choice.**

```console
$ jmatch invoices.txt payments.txt "the payment that settles this invoice"
Invoice #1042 Acme Corp $500	Payment received: Acme Corporation, 500 USD	0.930
Invoice #1043 Globex $1200	Payment received: Globex, 1200 USD	0.960

$ jmatch questions.txt faq.txt "the FAQ entry that answers the question" --format "{a} => {b}" --unmatched
```

## Usage

```
jmatch [options] FILE_A FILE_B DESCRIPTION
```

Either file may be `-` for standard input (not both).

| option | meaning |
|---|---|
| `-p P` | probability needed to count as a match (default 0.5) |
| `--unmatched` | also print lines of FILE_A that matched nothing (empty `{b}`, `{score}` is `-`) |
| `--format FMT` | output template with `{a}`, `{b}`, `{score}`, `{a_line}`, `{b_line}` (default `{a}\t{b}\t{score}`) |
| `--group K` | candidates compared per call (default 12, max 40) |
| `--shortlist M` | only consider the M candidates sharing the most words with each A line (a free prefilter, not embeddings) |
| `--json` | `{"a", "b", "score", "matched", "a_line", "b_line", "judged"}` per A line |

## How it works

Each A line is the `"target"`; B lines are `"candidates"` sent `--group` at a time as
`{"target", "candidates": [{"id", "text"}]}` with a choice question that includes a **`none`**
option. Group winners meet in a final round. If `none` wins or the winner is below `-p`, the
line is unmatched. All A lines run concurrently.

Cost is about |A| × ⌈|B| / K⌉ calls: 100 × 100 lines is about 900 calls, a few cents.
`--shortlist 24` cuts that to 2-3 calls per A line for large B files.

## Behaviour

- Many-to-one is allowed: several A lines may match the same B line.
- Fail-open: an A line whose calls failed prints as unmatched (with `--unmatched`) and the exit
  status becomes 5.
- Exit status: 0 at least one match, 1 none, 2 usage, 3 auth, 4 API, 5 partial.
