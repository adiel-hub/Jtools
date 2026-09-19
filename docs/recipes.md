# Recipes

Pipelines people actually run. Every tool reads stdin and writes stdout, so they compose with
each other and with coreutils.

## Triage

```bash
# Angriest customers first, top ten, with scores
cat feedback.txt | jsort "angriest customer" -n 10 --with-score

# Route an inbox into files, then work the sales bucket by intent
cat inbox.txt | jroute "sales:a sales lead" "support:a support request" "spam:junk" -o sorted
jsort "highest buying intent" sorted/sales.txt | head -5

# Label tickets and count the labels
cat tickets.txt | jtag --labels "bug,feature,question,complaint" | cut -f1 | sort | uniq -c
```

## Operations

```bash
# Alert-fatigue killer: only lines a human should see, collapsed per five minutes
tail -f app.log | jwatch "something a human should look at right now" --cooldown 300 --bell

# Page only on repeated failures, with a notification per alert
journalctl -f -u api | jwatch "a service is failing repeatedly" --exec 'notify-send "api" {}'

# Gate a deploy on the last fifty lines of a canary log
tail -50 canary.log | jgate "no errors that a customer would notice" && ./promote.sh

# The ten most relevant kernel lines, in order
dmesg | jhead 10 "a hardware error or a device that failed"
```

## Code review and git

```bash
# Merge only if Jev is confident the diff is safe (fails closed on API errors)
git diff origin/main | jgate -p 0.9 "a change that is safe to merge without review" && git merge

# TODOs that are really bugs, across a repo
jgrep -r --glob '*.py' "a TODO or FIXME describing an actual bug, not a nicety" src/

# Which of these commit messages best describes the diff?
git log --oneline -20 | jpick "the commit that introduced the retry logic" --why
```

## Data cleaning

```bash
# Dedupe feature requests that mean the same thing, biggest groups first
cat requests.txt | juniq "same underlying request" -c | sort -rn | head

# Join invoices to payments by meaning; show what did not match
jmatch invoices.txt payments.txt "the payment that settles this invoice" --unmatched

# Judge one CSV column but keep whole rows
jgrep --csv --field abstract "uses a natural experiment" papers.csv > selected.csv

# Judge one JSONL field, return full records
jgrep --jsonl --field message "a payment failed" events.jsonl

# Judge the whole record, so one description can weigh several fields at once
jgrep --jsonl "a production deploy made outside working hours by a bot" deploys.jsonl
```

## Composition

```bash
# Rank, then keep only the confident top, then tag the survivors
cat leads.txt | jsort "ready to buy" --with-score | awk -F'\t' '$1 > 0.7 {print $2}' | jtag --labels "enterprise,smb"

# Score a whole scale explicitly
cat reviews.txt | jtag --score "sentiment" --levels "furious,unhappy,neutral,happy,delighted" --suffix

# Know what will be sent before you send it
cat customers.csv | jsort "most likely to churn" --dry-run

# Reproducible results: pin the model and keep the cache
cat data.txt | jsort "..." --model jev-1.13.0
```

## Cost and speed controls

```bash
--budget 5        # stop at five dollars (default 1; JEV_BUDGET=20 for a long-lived tail -f)
-j 32             # more requests in flight (default 20)
--no-cache        # measure fresh; reruns are otherwise free
--stats           # print calls, tokens, dollars, p50 latency to stderr
--timeout 30      # allow slower rate-limited backends more time per request
```
