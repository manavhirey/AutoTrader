"""orb_bot — single-timeframe Opening Range Breakout trading bot for Alpaca.

Flat-layout package (Hitchhiker's Guide): this package lives at the repository
root. The pure core (`models`, `aggregation`, `indicators`, `engine`) imports
nothing from the I/O layers (`feed`, `execution`, `approval`, `reporting`,
`orchestrator`, `discordbot`); that boundary is enforced by import-linter.
"""

__version__ = "0.1.0"
