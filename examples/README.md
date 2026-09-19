# Examples

Small inputs used by the README demos and by `scripts/record_demos.py`. Try them:

```bash
jsort "angriest customer first" examples/feedback.txt --with-score
jroute "sales:a sales lead" "support:a support request" "spam:junk or a scam" -i examples/inbox.txt -o /tmp/sorted
jwatch "something an operator should look at right now" examples/app.log
juniq "the same feature request" examples/requests.txt -c
jtag --labels "bug,feature,question,complaint" examples/tickets.txt
jmatch examples/invoices.txt examples/payments.txt "the payment that settles this invoice" --unmatched
jhead 3 "a decision that was made" examples/thread.txt
jpick "the most urgent thing to fix" examples/tickets.txt --why
cat examples/app.log | jgate "mentions a security incident" && echo "page security"
jgrep -o "a payment problem" examples/inbox.txt
```
