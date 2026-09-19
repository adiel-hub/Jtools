# jhead

The N most relevant lines, in their original order. **Primitive: score.**

```console
$ cat long-thread.txt | jhead 5 "the key decisions made"
$ dmesg | jhead 10 "hardware errors"
$ jhead 3 "action items" notes.txt --reorder --with-score
```

Like `head`, but relevance decides which lines survive, not position. jsort reorders everything;
jhead keeps input order and cuts to N.

## Usage

```
jhead [options] N DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `--tail` | the N **least** relevant lines instead |
| `--reorder` | sort the survivors by score instead of input order |
| `-s`, `--with-score` | prefix each line with its 0..1 score |
| `--levels CSV` | your own ordered rubric, lowest first |
| `--json` | `{"rank", "line", "score", "level", "confidence", "source", "lineno"}`; `rank` is the relevance rank, so `jq 'select(.rank==1)'` names the best line whatever order the lines came out in |

## Behaviour

- Reads everything, one call per line, all concurrent. `jhead 0 …` prints nothing and makes no
  calls.
- Ties break by input order.
- Lines that could not be judged are never selected; the exit status becomes 5.
- Exit status: 0 output produced, 1 no input or nothing selectable, 2 usage, 3 auth, 4 API, 5 partial.
