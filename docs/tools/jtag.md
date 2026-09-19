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
| `--labels CSV` | labels, optionally described: `"bug,feature:new capability,question"`. A description may
contain commas (`"world:politics, war and diplomacy,sports:games"`): a comma only starts a new label when what
follows looks like `name:`. Write `\,` for a literal comma. |
| `--label NAME:DESC` | one label, repeatable. No comma rules at all; use it when a description is complex |
| `--score DESC` | what to rate; a parenthesised range `(0-100)` or `from 1 to 5` in it sets the output scale (a bare `2024-2025` in prose does not) |
| `--sep CHAR` | column separator (default tab; `\t`, `,`, `|` work) |
| `--suffix` | put the new column last instead of first |
| `--with-prob` | also write the label's probability, or the score's confidence. With `--csv`/`--jsonl` it becomes its own column (`label_probability`, `score_confidence`) rather than a second value inside the first |
| `--default LABEL` | labels mode: use this label when the best label's probability is below `-p` |
| `--scale LO-HI` | score mode: rescale the 0..1 score explicitly |
| `--levels CSV` | score mode: your own ordered rubric, lowest first |
| `--json` | labels: `{"line", "label", "probability", "probabilities"}`; score: `{"line", "score", "value", "level", "confidence"}`. A blank input line is an object too, marked `"blank": true`, so every output line parses; a line that could not be judged carries `"judged": false` |
| `--jsonl`, `--csv` | read [records, not lines](../structured.md): the header is not labelled as data, and the verdict becomes a real CSV column or JSON key rather than text glued on with `--sep` |
| `--field NAME` | with `--jsonl`/`--csv`: judge only this value (dotted JSON paths work), still printing the whole record |
| `--column NAME` | name the added column or key (default `label`, or `score` in score mode). A name the record already uses is reported on stderr — run jtag over its own output and the old value is replaced |

## Behaviour

- Streams and keeps input order; one call per line, `-j` in flight.
- Blank lines pass through unchanged. Lines that could not be judged get `-` in the column and
  the exit status becomes 5.
- Scores print with two decimals on 0..1, as integers on ranges of ten or more, else one decimal.
- Exit status: 0, 1 no input, 2 usage, 3 auth, 4 API, 5 partial.

## vs. jroute

jtag labels lines in place. jroute physically separates the stream into files.
