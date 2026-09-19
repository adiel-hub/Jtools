# Structured input: `--jsonl` and `--csv`

Every j-tool reads lines. A CSV export and a JSONL log are not lines — they are **records**, and
reading them as lines goes wrong in two places at once:

```console
$ jsort "how urgent this ticket is" examples/tickets.csv      # without --csv
1004,hooli,enterprise,"I was charged twice for one order, and nobody answers"
1008,wayne,free,"Your support is a joke, three days without a reply"
1006,acme,enterprise,Login fails with error 500 since this morning
…
id,customer,plan,message                                      ← the header, ranked as data
```

The header row cost a real decision (nine calls for eight tickets), came back with a verdict, and
was placed by it — here last, because "id,customer,plan,message" is not an urgent ticket. Wherever
it lands, that is no longer a file a spreadsheet will open.

`--csv` and `--jsonl` fix both halves:

```console
$ jsort "how urgent this ticket is" --csv examples/tickets.csv
id,customer,plan,message
1004,hooli,enterprise,"I was charged twice for one order, and nobody answers"
1006,acme,enterprise,Login fails with error 500 since this morning
1008,wayne,free,"Your support is a joke, three days without a reply"
…
```

## What is judged

| | what Jev is shown |
|---|---|
| `--csv` | the row as an object, keyed by the header: `{"id": "1", "customer": "acme", …}` |
| `--jsonl` | the JSON value on that line, as the object (or array) it already is |
| `--field NAME` | just that one value, as text |

A whole record goes to Jev **as an object**, not as a string that happens to contain JSON. That is
the point of a decision model that reads JSON natively: one description can weigh several columns
at once — `"a paying customer who is blocked"` looks at the plan *and* the message — without you
having to paste them together first.

`--field` narrows it to one value when the rest of the row is noise. For JSONL it takes a dotted
path, so `--field user.profile.bio` reaches a nested field. `--field` without `--jsonl` or `--csv`
is a usage error: there are no fields in a line.

## What comes out

Records go out the way they came in, so the output is still a file you can open:

| tool | output |
|---|---|
| `jgrep` | matching rows, with the header first (once per file) |
| `jsort`, `jhead`, `jpick`, `juniq` | the rows that survived, header first |
| `jtag` | the row plus one column: a new CSV column, or a new JSON key (`--column NAME` names it). `--with-prob` adds a second column, not a second value inside the first. A number written into a JSON record is a number, so `jq 'select(.score > 0.9)'` works |
| `jroute` | one file per bucket, `bug.csv` / `bug.jsonl`, each with its own header |
| `jgate -P` | the input copied through, header and all |
| `jmatch` | the two matched rows, joined by `--format` |
| `jwatch` | an alert feed — scores, markers, a bell — so **no** header; it is not a file to reopen |

`--json` overrides all of it: a bare CSV header line in the middle of JSON output would be
unparseable, so it is never written there.

**A prefixed row gets no header.** `jsort -s`, `jhead -s`, `jpick -s`, `juniq -c`,
`juniq --show-groups`, `jgrep -o`, `jgrep -n` and a file-name prefix all put something in front of
the record. The columns then no longer line up with the header, so printing one would claim more
than the output can keep; you get the view you asked for, without a header pretending it is a
file. To put a verdict *inside* the record and keep a real CSV, that is what `jtag` does:

```console
$ jtag --csv --score "how urgent this is" --column urgency examples/tickets.csv
urgency,id,customer,plan,message
0.59,1001,acme,enterprise,The app crashes when I open settings
0.17,1002,globex,free,Could you add a dark theme?
0.79,1004,hooli,enterprise,"I was charged twice for one order, and nobody answers"
```

## Per-tool notes

- **`jmatch`** reads both files as records. Two exports rarely name a column the same way, so
  `--field` speaks for FILE_A and `--field-b` for FILE_B.
- **`jgate`** needs `--each` or `--all`: without them the whole input is one state, and a header
  row is part of the document being judged.
- **`jroute`** names its buckets after the input — `.csv` or `.jsonl` instead of `.txt` — and
  writes a CSV header only when *this* run starts the file, so appending to yesterday's bucket
  does not drop a second header into the middle of it. `--ext` still overrides the name.

## Errors and edges

- A column that is not in the header, or a row with too few columns, is an input error: exit 2,
  naming the file and the line. Nothing is guessed.
- Duplicate column names are refused rather than silently collapsed.
- A malformed JSON line is reported and skipped; the rest of the file is still judged.
- A record longer than `--max-chars` is sent as clipped JSON **text** rather than as an object: an
  object has no "first N characters", and reporting a record as truncated while sending all of it
  would be a lie. It is still *printed* whole — the clip decides what Jev reads, never what is
  written back, so a row is never cut off mid-quote.
- Blank lines and blank rows are skipped, as everywhere else.
- Quoting is normalised on the way out: `csv` writes the minimal correct quoting, so a cell that
  did not need quotes loses them. The values are unchanged.
- Two inputs whose headers differ are reported on stderr. `jgrep` writes one header per file,
  because it never reorders; the tools that interleave every input's rows carry the first header
  and say so, since no single one describes the output.
- `jtag` says so on stderr when the column it adds is a name the record already uses — which is
  what running it over its own output does.
- No colour reaches a cell. A CSV cell or a JSON string holding an escape sequence is not the
  value, so in these modes the verdict is written plain even on a terminal.

## Cost

One fewer decision per file, and usually a shorter state: `--field` sends one value instead of a
whole row. On the 5,574-message spam corpus, judging the message field rather than the raw line
was within noise on accuracy — the saving here is the header call and a valid file, not accuracy.
