# Contributing

Contributions (pull requests) are very welcome! Here's how to get started.

---

**Getting started**

First fork the library on GitHub.

Then clone and install the library:

```bash
git clone https://github.com/your-username-here/tinyio.git
cd tinyio
pip install -e '.[dev]'
pre-commit install  # `pre-commit` is installed by `pip` on the previous line
```

---

**If you're making changes to the code:**

Now make your changes. Make sure to include additional tests if necessary.

Next verify the tests all pass:

```bash
pip install -e '.[tests]'
pytest  # `pytest` is installed by `pip` on the previous line.
```

Then push your changes back to your fork of the repository:

```bash
git push
```

Finally, open a pull request on GitHub!

