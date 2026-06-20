# orb-bot

A single-timeframe **Opening Range Breakout (ORB)** trading bot for Alpaca.

A single `asyncio` event loop streams 1-minute bars, aggregates them to the
strategy timeframe `T` (default 15m, anchored to the 09:30 open), and feeds a
**pure, deterministic state machine** (`orb_bot/engine.py`). The engine derives
all timing from `candle.ts_close` (never the wall clock), so paper trading,
live trading, and future backtests share one core. Paper mode auto-approves
setups; live mode gates each setup behind a Discord approval. Both modes emit an
end-of-session P/L report.

## Layout

Flat layout (Hitchhiker's Guide): the `orb_bot/` package lives at the repository
root. Pure modules (`models`, `aggregation`, `indicators`, `engine`) import
nothing from the I/O layers (`feed`, `execution`, `approval`, `reporting`,
`orchestrator`, `discordbot`); the boundary is enforced by `import-linter`.

## Requirements

- Python 3.11+
- An Alpaca account (paper or live) and API keys
- (Live mode) A Discord bot token, channel, and approver user

## Setup

```bash
make init                 # installs deps + the package (editable, [dev] extras)
cp .env.example .env      # then fill in your secrets
```

Secrets live in `.env` (gitignored): `ALPACA_KEY`, `ALPACA_SECRET` are always
required; `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_APPROVER_USER_ID` are
required in live mode and optional in paper. Strategy and run flags live in
`config/config.yaml` (committed; no secrets).

## Run

```bash
make run                  # equivalently: python -m orb_bot
```

Edit `config/config.yaml` to set the `run.symbol`, switch `run.live`, choose the
data `run.feed` (`IEX`/`SIP`), and tune the strategy parameters. Configuration
is validated at load time, so a bad config fails before any network connection.

## Develop

```bash
make test                 # pytest tests
make lint                 # ruff + mypy + import-linter (purity contract)
```
