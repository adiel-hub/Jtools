# jgrep

grep, but the pattern is a description. **Primitive: noul** (one yes/no question per record).

```console
$ tail -f app.log | jgrep "a user is getting frustrated"
user 12: this is the third time checkout has failed, I am done with this app
user 77: WHY does it log me out every five minutes??

$ jgrep -o "announces or releases a new AI model" titles.txt | sort -rn | head -3
0.980	PrismML Launches Bonsai 2 27B, Its Most Capable Model Yet
0.970	Alibaba Releases Qwen3.8-Omni-Flash
0.940	Google announces new experimental "CC" AI agent for families
```

## Usage

```
jgrep [options] DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `-e DESC` | another description; all go in **one call** per line. A line matches if any fits, or all with `--all` |
| `-p P`, `--prob P` | match when the probability is at least P (default 0.5) |
| `-v`, `--invert-match` | print lines that do **not** match |
| `-o` | put the probability in a first, tab-separated column |
| `-n`, `--line-number` | prefix each line with its line number |
| `-H`, `--with-filename`, `--no-filename` | show or hide the file name |
| `-c`, `--count` | print how many lines matched, not the lines |
| `-l`, `--files-with-matches` | print the names of files with a match |
| `-L`, `--files-without-match` | print the names of files with **no** match. The complement of `-l` — `-l -v` is a different question (files holding at least one non-matching line) |
| `-m NUM`, `--max-count NUM` | stop after NUM matches per file |
| `-q`, `--quiet` | print nothing, stop at the first match, let the exit status answer |
| `-r`, `--recursive`, `--glob PAT`, `--exclude PAT`, `--hidden` | recursive search; skips VCS/dependency dirs and binaries |
| `--para`, `--whole` | judge paragraphs or whole files |
| `-A N`/`--after-context`, `-B N`/`--before-context`, `-C N`/`--context` | print N records after / before / either side of each match, as grep does: `-` where a match has `:`, and `--` between runs that are not adjacent. What matched does not change |
| `--judge-context N` | the different thing: show Jev the N records either side **when deciding**. Still one decision per record, and only that record prints |
| `--jsonl`, `--csv` | judge the whole record, and print it. Jev reads a JSON object natively, so the record goes as an object rather than as text that happens to contain JSON — which is what lets one description weigh several fields at once |
| `--field NAME` | with `--jsonl` or `--csv`: judge only this value, still printing the full record (dotted JSON paths work) |
| `--json` | one JSON object per match: `{"file", "line", "p", "text", ["ps"], ["record", "field"]}` |
| `--unordered` | print as answers arrive instead of in input order |

The flags above that grep also has mean what they mean in grep, `-q` included: it prints nothing,
stops at the first match and lets the exit status be the answer. In every other j-tool `-q` only
silences the end-of-run notes on stderr.

Plus the [common options](../../README.md#common-options) every tool has.

## Behaviour

- Streams: a line is judged the moment it arrives; `tail -f` works. Output is in input order.
- Blank lines never match and cost nothing.
- Fail-open: a line that could not be judged **passes through** (not with `-v`) and the exit
  status becomes 5. `--strict` stops at the first error with exit 4.
- Exit status: 0 matched, 1 nothing matched, 2 usage or file error, 3 auth, 4 API, 5 partial.

## Notes

- Jev answers the description you wrote. "a complaint" and "an angry complaint" are different
  filters. Borderline lines get probabilities in the middle; that is what `-p` is for.
- `--judge-context N` costs about 2N+1 lines of tokens per decision, and on a live stream a line waits for the
  N lines after it.
- Each description costs about 27 extra tokens and no extra time; five `-e` filters in one pass
  cost about the same as one.
