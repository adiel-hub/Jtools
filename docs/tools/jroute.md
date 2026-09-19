# jroute

Split a stream into buckets. **Primitive: choice.**

```console
$ cat inbox.txt | jroute "sales:a sales lead" "support:a support request" "spam:junk" --out-dir sorted
jroute: 412 lines routed (support: 231, sales: 118, spam: 63) -> /work/sorted

$ tail -f events.log | jroute "alert:needs a human now" "noise:routine chatter" --stdout alert | notify
```

Each line is classified once and appended to `OUT_DIR/<bucket>.txt`. Files are created on first
use. Nothing is ever lost: lines below the threshold go to `--default`, lines that could not be
judged go there too, or to `unrouted.txt`.

## Usage

```
jroute [options] "NAME:DESCRIPTION" "NAME:DESCRIPTION" ... [-i FILE]
```

| option | meaning |
|---|---|
| `-i`, `--input FILE` | read this file instead of stdin (repeatable) |
| `-o`, `--out-dir DIR` | where bucket files go (default: current directory) |
| `--ext EXT` | bucket file extension (default `.txt`) |
| `--default NAME` | bucket for lines below `-p` and for unjudged lines; `-p` requires it |
| `--stdout NAME` | send only this bucket's records to standard output, in `--json` too |
| `--truncate` | start bucket files empty instead of appending; nothing is emptied until the first line is routed, so a run that reads nothing leaves the previous run's files alone |
| `--no-files` | write no files (use with `--json` or `--stdout`, not both: every other record would exist nowhere) |
| `--json` | `{"bucket", "line", "probability", "probabilities", "source", "lineno"}` per line |
| `-q` | no summary line on stderr |
| `-v` | one line per routed line: the bucket, the probability, the text |

Bucket names may use letters, digits, `.`, `-` and `_`.

## Behaviour

- Streams; files are line-buffered so `tail -f events.log | jroute …` fills them live.
- Order within each bucket file is input order.
- Exit status: 0, 1 empty input, 2 usage, 3 auth, 4 API, 5 partial (some lines unjudged).
