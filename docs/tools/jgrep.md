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
| `-p P` | match when the probability is at least P (default 0.5) |
| `-v` | print lines that do **not** match |
| `-o` | put the probability in a first, tab-separated column |
| `-n`, `-H`, `--no-filename`, `-c`, `-l`, `-m NUM`, `-q` | as in grep |
| `-r`, `--glob PAT`, `--exclude PAT`, `--hidden` | recursive search; skips VCS/dependency dirs and binaries |
| `--para`, `--whole` | judge paragraphs or whole files |
| `-C N` | show Jev the N records either side; still one decision per record, and only that record prints |
| `--jsonl --field NAME`, `--csv --field NAME` | judge one field, print the full record (dotted JSON paths work) |
| `--json` | one JSON object per match: `{"file", "line", "p", "text", ["ps"], ["record", "field"]}` |
| `--unordered` | print as answers arrive instead of in input order |

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
- `-C N` costs about 2N+1 lines of tokens per decision, and on a live stream a line waits for the
  N lines after it.
- Each description costs about 27 extra tokens and no extra time; five `-e` filters in one pass
  cost about the same as one.
