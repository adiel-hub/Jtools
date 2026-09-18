---
name: Tool proposal
about: Propose a new j-tool (read CONTRIBUTING.md's Hard Rule first)
labels: proposal
---

**One-line job** (a *decision*, not a generation):

**Which Jev primitive** does it map to? `noul` / `choice` / `score`

**Why can't an existing tool do it?** (jgrep, jsort, jpick, jgate, jwatch, juniq, jhead, jtag, jroute, jmatch)

**The demo that sells it:**

```console
$ ... | jnew "..."
```

**Hard Rule check:** the tool never asks the model to *write* anything and never does embedding
similarity. Confirm: [ ]
