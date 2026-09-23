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
title: Fixture Executable Article
source: fixture
---

# Fixture Executable Article

## Finding

The prose-only sentinel is EXECUTABLE_PROSE_SENTINEL.
"""
    )
    return


@app.cell
def _():
    CODE_ONLY_IDENTIFIER_SENTINEL = "this must not enter chunks"
    return (CODE_ONLY_IDENTIFIER_SENTINEL,)


if __name__ == "__main__":
    app.run()
