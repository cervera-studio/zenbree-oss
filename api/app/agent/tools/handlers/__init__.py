"""Importing this package registers every tool (see docs/contracts-m1.md).

A handler module registers itself with ``register(ToolDef(...))`` at import, so
every public module here is imported: a new handler needs no edit in this file.
"""

import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    if not _module.name.startswith('_'):
        importlib.import_module(f'{__name__}.{_module.name}')
