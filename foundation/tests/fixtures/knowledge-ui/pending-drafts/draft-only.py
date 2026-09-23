# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "marimo",
# ]
# ///
import marimo

__generated_with = "0.14.17"
app = marimo.App()


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md(
        """
---
title: Draft Only Executable
source: fixture
---

# Draft Only Executable

Draft prose.
"""
    )
    return


if __name__ == "__main__":
    app.run()
