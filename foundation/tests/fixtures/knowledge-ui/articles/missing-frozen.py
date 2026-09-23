import marimo

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
title: Missing Frozen View Article
source: fixture
---

# Missing Frozen View Article

## Note

This executable article intentionally has no frozen HTML.
"""
    )
    return


if __name__ == "__main__":
    app.run()
