import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md("# Orphan Executable Note")
    return


if __name__ == "__main__":
    app.run()
