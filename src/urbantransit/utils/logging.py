# logging utilities

import logging

transitlog = logging.getLogger("GTFS")

# Remove all existing handlers - NOTEBOOK ONLY
for handler in transitlog.handlers[:]:
    transitlog.removeHandler(handler)

transitlog.setLevel(logging.DEBUG)
console = logging.StreamHandler()
console.setLevel(level=logging.DEBUG)
transitlog.addHandler(console)