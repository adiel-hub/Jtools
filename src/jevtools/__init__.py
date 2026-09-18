"""j-tools: semantic judgment for the shell.

Ten small commands, one decision each, all built on Jev's three primitives (noul, choice, score):

    jgrep   filter lines that fit a description          jhead   the N most relevant lines, in order
    jsort   rank lines by how well they fit               jtag    add a label or score column
    jpick   choose the single best line                   jroute  split a stream into bucket files
    jgate   exit status from a yes/no judgment            jmatch  semantic join of two files
    jwatch  alert only on lines worth attention           juniq   drop lines that mean the same
"""

from __future__ import annotations

from jevcore import __version__

TOOLS: dict[str, str] = {
    "jgrep": "print lines that fit a description",
    "jsort": "sort lines by how well they fit a description",
    "jpick": "choose the single best line for a description",
    "jgate": "set the exit status from a yes/no judgment of the input",
    "jwatch": "print (and act on) only the lines worth attention in a live stream",
    "juniq": "drop lines that mean the same as an earlier line",
    "jhead": "the N most relevant lines, in their original order",
    "jtag": "add a label column (choice) or a score column (score) to every line",
    "jroute": "split a stream into bucket files by category",
    "jmatch": "join two files by meaning: for each line in A, the best line in B",
}

__all__ = ["TOOLS", "__version__"]
