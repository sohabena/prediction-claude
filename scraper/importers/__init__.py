"""Importers package for external data sources.

Modules are imported by name (``from scraper.importers import oddspapi``); nothing is loaded eagerly, so the
historical-odds importers (S99 to S101) never load the retired Betfair importer, whose data is never used
(Golden Rulebook rule 3).
"""
