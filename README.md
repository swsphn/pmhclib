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
PMHC_TOTP_SECRET
```

Otherwise, you will be prompted for credentials interactively.

In PowerShell, you can set the environment variables interactively as
follows:

``` ps1
$env:PMHC_USERNAME='your_username_here'
$env:PMHC_PASSWORD=python -c 'import getpass; print(getpass.getpass())'
$env:PMHC_TOTP_SECRET=python -c 'import getpass; print(getpass.getpass("TOTP Secret: "))'
```

In a Unix shell (Mac, Linux), you can do:

``` bash
export PMHC_USERNAME='your_username_here'
read -rs PMHC_PASSWORD && export PMHC_PASSWORD
read -rs PMHC_TOTP_SECRET && export PMHC_TOTP_SECRET
```

NOTE: `PMHC_TOTP_SECRET` is the unchanging base32-encoded TOTP secret,
not the time-based six-digit code. You can likely find this secret in
the 'advanced' section of your TOTP app. It will be a long string of
upper-case letters and digits. See below for a list of TOTP apps which
support viewing the TOTP secret. It is also possible to get the secret
by scanning the setup QR code, or by clicking the button on the website
to manually configure the TOTP app. The six-digit code will be
automatically calculated based on the current time as required if
`PMHC_TOTP_SECRET` is specified. Otherwise, the user will be prompted to
enter the current six-digit code.

Not all TOTP apps support viewing the secret. The following are known
to support this:

- [Aegis Authenticator](https://getaegis.app/) (Android only)
- [Bitwarden
  Authenticator](https://bitwarden.com/products/authenticator/)
- [Ente Auth](https://github.com/ente-io/ente/tree/main/auth#readme)
- [2FA Authenticator (2FAS)](https://2fas.com/)

For more details, see the [list of recommended authenticator
apps][mfa-apps] on our Data Wiki.

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
[mfa-apps]: https://datawiki.swsphn.com.au/software/gui-tools/multi-factor-authentication-apps/
