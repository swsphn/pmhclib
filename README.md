# pmhclib

pmhclib is a Python wrapper for the unofficial PMHC MDS portal API. You
can use it to automate uploads and downloads to the PMHC portal.

## Install

This script is setup as a Python package. You should be able to install
it directly with `pip` or `poetry`:

``` sh
pip install pmhclib@git+https://github.com/swsphn/pmhclib.git
```

OR

``` sh
poetry add pmhclib@git+https://github.com/swsphn/pmhclib.git
```

This will install `pmhclib` as an importable Python library.

## Usage

`pmhclib.PMHC` is intended to be used with a context manager. This
ensures that the web browser which performs the login process and the
API requests is correctly shut down when the script exits. The standard
use pattern is as follows:

``` python
from pmhclib import PMHC
with PMHC() as pmhc:
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

## Documentation

Review the built-in documentation from Python as follows:

``` python
>>> from pmhclib import PMHC
>>> help(PMHC)
```
