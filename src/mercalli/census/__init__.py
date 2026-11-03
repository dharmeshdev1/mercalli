"""Dependency census: which DNS, edge, hosting, certificate and email providers domains rely on.

`census()` probes domains and returns one `DomainRecord` each, `summarize()` turns records into
shares and concentration figures, and `write_outputs()` saves both. `main()` is the command line
entry point behind `python -m mercalli.census`.
"""

from mercalli.census.cli import main
from mercalli.census.models import DomainRecord
from mercalli.census.output import write_outputs
from mercalli.census.refdata import ReferenceData, load_reference_data
from mercalli.census.run import census
from mercalli.census.summary import summarize

__all__ = ["DomainRecord", "ReferenceData", "census", "load_reference_data", "main", "summarize", "write_outputs"]
