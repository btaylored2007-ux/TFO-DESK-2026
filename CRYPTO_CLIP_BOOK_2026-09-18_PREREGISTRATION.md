# Crypto Clip Book — preregistration (PAPER)

**As of:** 2026-09-18T19:37:39+10:00
**Label:** opportunistic beta (not validated alpha)
**Retail-executable:** yes — Kraken USD pairs

## Capital
- Seed: $80,044.27
- Sources: GCL dry powder + Kraken engine idle cash (engine start_cash reduced so residual equity ~$25)
- Cash buffer: $2,000.00

## Composition (Bot-owned entries/exits)
| Pair | Units | Entry | Take (+8%) | Stop (−4%) | ~Notional |
|------|------:|------:|----------:|----------:|----------:|
| UNI/USD | 2603.8481 | 8.9918 | 9.711144 | 8.632128 | $23,413 |
| ARB/USD | 93893.4913 | 0.2078 | 0.224424 | 0.199488 | $19,511 |
| NEAR/USD | 5670.8328 | 3.4406 | 3.715848 | 3.302976 | $19,511 |
| SOL/USD | 147.4063 | 105.89 | 114.3612 | 101.6544 | $15,609 |

## Book rails
- Take: +$5,000 OR +15%
- Stop: −5% OR −$2,500
- Per-name: +8% / −4% (tightened; UNI/ARB already extended on 24h)

## Thesis / falsifier
Rotate passive BTC/ETH/SOL into liquid 24h RS: UNI+ARB (DeFi/L2) + NEAR (deep strong mover) + SOL liquidity anchor. Bot-owned entries/exits.

**Falsifier:** Basket lags XBT 24h OR book stop −5%/−$2500 OR any name 24h notional <$2M

## Skipped movers
G (+55% illiquid), STRK (thin), TAO/SUI (capacity / overlap).
