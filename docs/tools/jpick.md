# jpick

Choose the single best line. **Primitive: choice** (candidates compared against each other).

```console
$ cat subject-lines.txt | jpick "most likely to get opened" --why
Your invoice is ready (and 40% smaller)	# p=0.71 vs 0.18 for “Weekly product digest”

$ ls candidates/*.pdf | jpick "best fit for a senior backend role" --top 3 --with-score
```

jsort rates each line alone; jpick shows Jev the candidates **together** and asks which one fits
best. That is a different question and often a sharper one.

## Usage

```
jpick [options] DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `--top N` | return the N best instead of one (default 1) |
| `--why` | append the winning probability and the runner-up, taken from Jev's distribution |
| `-s`, `--with-score` | prefix each line with its final-round probability |
| `--group K` | candidates compared per call (default 12, max 40) |
| `--json` | `{"rank", "line", "probability", "runner_up", "runner_up_probability", "source", "lineno"}` |
| `--jsonl`, `--csv` | read [records, not lines](../structured.md): the header is not a candidate, and the winner comes back with it |
| `--field NAME` | with `--jsonl`/`--csv`: judge only this value (dotted JSON paths work), still printing the whole record |

## How it works

Candidates are sent `--group` at a time as a JSON array of `{"id", "text"}` with a choice
question over the ids. Group winners (top N with `--top N`, always dropping at least one) advance;
rounds repeat until one group remains, whose distribution is the final ranking. That final group
may be larger than `--group` (up to 40) when that is what it takes to hand back N lines. 1,000 lines with the default group of 12
take about 90 calls; 12 lines take one.

`--why` is not generated text. It is the winner's probability and the runner-up from the same
distribution, which is the honest reason a decision model can give.

## Behaviour

- One line in, one line out, no call made.
- A failed comparison call keeps its whole group (nobody is dropped on an error); if nothing
  can be judged at all, jpick falls back to input order and exits 5.
- Exit status: 0, 1 no input, 2 usage, 3 auth, 4 API, 5 partial.
