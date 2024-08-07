# pmhclib

pmhclib is a Python wrapper for the unofficial PMHC MDS portal API. You
can use it to automate uploads and downloads to the PMHC portal.

## Install

`pmhclib` is a Python package. You should be able to install
it directly with `pip` or `poetry`:

``` sh
pip install pmhclib@git+https://github.com/swsphn/pmhclib.git
```

OR

``` sh
poetry add pmhclib@git+https://github.com/swsphn/pmhclib.git
```

This will install `pmhclib` as an importable Python library.

NOTE: This package depends on [Playwright][] to authenticate to the PMHC
portal. Once the package is installed, you will need to install
a browser for Playwright to use. Make sure you are in the same Python
environment in which you installed `pmhclib`, then run:

```
playwright install chromium
```

## Usage

`pmhclib.PMHC` is intended to be used with a context manager. This
ensures that the web browser which performs the login process and the
API requests is correctly shut down when the script exits. The standard
use pattern is as follows:

``` python
from pmhclib import PMHC
with PMHC('PHN105') as pmhc:
    pmhc.login()
    ...
    # other pmhc methods.
```

`pmhc.login()` will read credentials from the following environment
variables if they are set:

```
PMHC_USERNAME
PMHC_PASSWORD
```

Otherwise, you will be prompted for credentials interactively.

In PowerShell, you can set the environment variables interactively as
follows:

``` ps1
$env:PMHC_USERNAME='your_username_here'
$env:PMHC_PASSWORD=python -c 'import getpass; print(getpass.getpass())'
```

In a Unix shell (Mac, Linux), you can do:

``` bash
export PMHC_USERNAME='your_username_here'
read -rs PMHC_PASSWORD && export PMHC_PASSWORD
```

## Documentation

See the [online documentation][docs].

### Built-in docs

Review the built-in documentation from Python as follows:

``` python
>>> from pmhclib import PMHC
>>> help(PMHC)
```

### Build docs

You can also generate html documentation locally using [Sphinx][] if you
have a local copy of the repository.

Linux:

```
cd docs
make html
```

PowerShell:

```
cd docs
./make.bat html
```

The generated documentation can be viewed at `docs/_build/html/index.html`.

[Playwright]: https://playwright.dev/python/
[Sphinx]: https://www.sphinx-doc.org/
[docs]: https://swsphn.github.io/pmhclib/
