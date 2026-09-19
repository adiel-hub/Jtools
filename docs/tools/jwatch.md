# jwatch

Alert only on lines worth attention in a live stream. **Primitive: noul, streaming.**

```console
$ tail -f app.log | jwatch "something a human should look at right now"
ERROR payment service failed: connection refused
WARN  disk temperature 71C on nvme0

$ journalctl -f | jwatch "a service is failing repeatedly" --exec 'notify-send {}'
$ kubectl logs -f deploy/api | jwatch "a customer-visible error" --cooldown 300 --bell
```

Alert fatigue, solved in one line: every line is judged as it arrives, only the ones that fit are
printed, and bursts collapse into one alert.

## Usage

```
jwatch [options] DESCRIPTION [FILE ...]
```

| option | meaning |
|---|---|
| `-p P` | probability needed to alert (default 0.5) |
| `--exec CMD` | shell command per alert; `{}` is the line, passed as an argument (appended if absent) |
| `--cooldown SEC` | after an alert, swallow further alerts for SEC seconds; the count is reported when it lifts |
| `--max N` | stop after N alerts |
| `-s`, `--with-score` | prefix each alert with its probability |
| `--bell` | ring the terminal bell |
| `--all-lines` | also print non-matching lines, indented; alerts stand out |
| `--json` | `{"line", "probability", "source", "lineno", "alert": true}` per alert |

## Behaviour

- Never buffers: a line is judged the moment it arrives and alerts print in input order.
  `-j` (default 20) bounds requests in flight; a quiet stream costs nothing.
- `--exec` commands run in the background and are given ten seconds to finish at exit.
- **The line is never part of the command.** `{}` becomes the shell parameter `"$1"` and the line
  is passed to `/bin/sh` as that parameter, so a log line containing `$(…)`, backticks or `;` is
  text, not code. Do not put your own quotes around `{}`: it is quoted already, and
  `"{}"` would expand to `""$1""`, where the line is unquoted again and splits on whitespace.
  jwatch refuses that spelling, wherever the quotes sit. To put the line inside a longer
  string, write `$1` yourself: `--exec 'notify-send "api: $1"'`. An empty `--exec` is
  refused too: it would leave the line as the command word.
- Fail-open: a line that could not be judged is skipped (never alerts) and the exit status
  becomes 5; a dead endpoint is reported once a minute, not once a line.
- Exit status: 0 at least one alert, 1 none, 2 usage, 3 auth, 4 API (`--strict`), 5 partial.

## Cost on a long tail

A line costs about <!--num:tokens_per_call_round-->300<!--/num--> tokens, or
<!--num:dollars_per_call-->$0.000013<!--/num--> at list price. Ten lines a second, all day, is about
$11. Set `JEV_BUDGET=20` (or `--budget 0` for no limit) for a monitor that must not stop at the
default $1 seat belt.
