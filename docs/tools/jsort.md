# jsort

Sort lines by how well they fit a description. **Primitive: score** (a five-rung fit scale).

```console
$ cat feedback.txt | jsort "angriest customer first" --with-score
0.950	WHY does it log me out every five minutes?? This is the worst.
0.800	This is the third time checkout has failed, I am done with this app.
0.150	Pricing question: do you offer enterprise seats?
0.050	Love the new dashboard, thanks team!
```

Nobody's embedding search can rank by *anger*. jsort can, because it asks a calibrated model to
rate every line on the same rubric and then sorts by the interpolated score.

## Usage

```
jsort [options] DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `--asc` | worst fit first |
| `-n N` | print only the first N lines of the sorted output |
| `-s`, `--with-score` | prefix each line with its 0..1 score |
| `--levels CSV` | your own ordered rubric, lowest first (default: a five-rung "fits the description …" scale) |
| `--json` | `{"rank", "line", "score", "level", "confidence", "source", "lineno"}` per line |

## Behaviour

- Reads everything (a sort has to), judges all lines concurrently, one call per line.
- Ties keep input order (stable sort). Blank lines are dropped.
- Lines that could not be judged sort **last** and make the exit status 5.
- Exit status: 0 output produced, 1 no input, 2 usage, 3 auth, 4 API, 5 partial.

## Rubric

The default question is *Rate how well the text fits this description: "…"* with the levels
`does not fit at all` … `fits perfectly`. The score is the probability-weighted rung, rescaled to
0..1. `--levels "calm,tense,panic"` replaces the scale; the description then names what is being
rated ("how stressed the writer sounds").

## vs. jhead

jsort reorders everything. jhead keeps input order and cuts to the N most relevant lines.
