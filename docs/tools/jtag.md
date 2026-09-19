# jtag

Add a label or a score column to every line. **Primitive: choice (labels) or score (score).**

```console
$ cat tickets.txt | jtag --labels "bug,feature,question,complaint"
bug	The app crashes on launch
feature	Please add export to CSV
question	How do I reset my password?

$ cat reviews.txt | jtag --score "how positive (0-100)"
95	Absolutely love it, thanks!
8	Worst purchase this year.
```

jtag never filters or reorders. It annotates. One tool, two modes: this intentionally absorbs
what would have been separate `jscore` and `jclassify` commands.

## Usage

```
jtag [options] (--labels CSV | --score DESCRIPTION) [FILE ...]
```

| option | meaning |
|---|---|
| `--labels CSV` | labels, optionally described: `"bug,feature:new capability,question"` |
| `--score DESC` | what to rate; a range written in it (`0-100`, `1 to 5`) sets the output scale |
| `--sep CHAR` | column separator (default tab; `\t`, `,`, `|` work) |
| `--suffix` | put the new column last instead of first |
| `--with-prob` | also write the label's probability, or the score's confidence |
| `--default LABEL` | labels mode: use this label when the best label's probability is below `-p` |
| `--scale LO-HI` | score mode: rescale the 0..1 score explicitly |
| `--levels CSV` | score mode: your own ordered rubric, lowest first |
| `--json` | labels: `{"line", "label", "probability", "probabilities"}`; score: `{"line", "score", "value", "level", "confidence"}` |

## Behaviour

- Streams and keeps input order; one call per line, `-j` in flight.
- Blank lines pass through unchanged. Lines that could not be judged get `-` in the column and
  the exit status becomes 5.
- Scores print with two decimals on 0..1, as integers on ranges of ten or more, else one decimal.
- Exit status: 0, 1 no input, 2 usage, 3 auth, 4 API, 5 partial.

## vs. jroute

jtag labels lines in place. jroute physically separates the stream into files.
