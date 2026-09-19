# j-tools documentation

- [README](../README.md): what it is, install, the ten tools, numbers
- [Recipes](recipes.md): pipelines people actually run
- [Architecture](architecture.md): the primitives, the wire formats, the client, the pipeline
- [Benchmarks](benchmarks.md): how the numbers were measured, and how to reproduce them
- [FAQ and things to know](faq.md)
- [Contributing](../CONTRIBUTING.md): the Hard Rule, layout, how to add a flag or a tool
- [Security](../SECURITY.md)

## Tools

| tool | page |
|---|---|
| jgrep  | [docs/tools/jgrep.md](tools/jgrep.md) |
| jsort  | [docs/tools/jsort.md](tools/jsort.md) |
| jpick  | [docs/tools/jpick.md](tools/jpick.md) |
| jgate  | [docs/tools/jgate.md](tools/jgate.md) |
| jwatch | [docs/tools/jwatch.md](tools/jwatch.md) |
| juniq  | [docs/tools/juniq.md](tools/juniq.md) |
| jhead  | [docs/tools/jhead.md](tools/jhead.md) |
| jtag   | [docs/tools/jtag.md](tools/jtag.md) |
| jroute | [docs/tools/jroute.md](tools/jroute.md) |
| jmatch | [docs/tools/jmatch.md](tools/jmatch.md) |
| jtools | [docs/tools/jtools.md](tools/jtools.md) |

`docs/demo/` holds the recorded runs behind the README's screenshots (JSON casts and asciinema
`.cast` files); `docs/assets/` holds the rendered GIFs, SVGs and charts. Regenerate with
`scripts/record_demos.py` (live), `scripts/render_demo.py` and `scripts/render_charts.py`.
