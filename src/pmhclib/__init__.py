# read version from installed package
from importlib.metadata import version
__version__ = version("pmhclib")

# Import classes to be available at the top level.
# Allows importing with:
#     from pmhclib import PMHC
# rather than
#     from pmhclib.pmhc import PMHC
from .pmhc import PMHC, PMHCSpecification
