# What a soak proves

A soak test runs a real JAM network for a long time under steady load, then checks that
nothing was lost and the chain stayed healthy. It finds the problems that appear after
twenty minutes, not the ones a unit test catches: a queue that fills slowly, memory that
grows by a few kilobytes per block, work that gets heavier as a backlog builds.

The soaks in this course are jamswap's (its `soak/` folder): a DEX under trading load on a
six-validator network.

## How a soak works

```text
 load generator ──orders──▶ DEX (off-chain matching, rounds of up to 48 orders)
                                 │  a round = one work-package
                                 ▼
                          JAM network: 6 validators (lasair and/or PolkaJam)
                            guarantee → assure → audit → accumulate → finalize
                                 │
                                 ▼
                     the DEX service's on-chain state (orders settled)
```

1. **The network comes up**: six validators on one genesis, with the DEX's service in it,
   and the DEX's off-chain server.
2. **Load for SECS seconds**: the load generator places signed buy and sell orders from six
   dev accounts, 12 crossing pairs a minute (1,440 orders an hour), 20% of them *sealed*
   (hidden until their round reveals them).
3. **The DEX batches orders into rounds.** Each round is a work-package the validators
   guarantee, make available, audit and accumulate. An order is settled for good once its
   block is finalized.
4. **Drain**: 180 seconds with no new load, so orders in flight can finish.
5. **Verdict**: the soak judges every order's fate, and the chain on every node.

## What it checks

| Check | Passes when | What a failure tells you |
|---|---|---|
| offered load | ≤ 0.01% of orders turned away | The DEX could not keep up and refused traffic (for example an account hit its open-order cap). |
| clearing SLO | ≥ 0.9999 | Of the orders that could trade, too few did. The DEX's main promise is broken. |
| SEALED zero-loss | 0 stuck | Some hidden orders never reached an end state between commit and reveal. |
| one head | every sample | Nodes disagreed on the chain head. |
| liveness | blocks advance | Some node stopped producing or importing blocks. |
| finality | 0 conflicts, stall < 1 epoch | Finality stalled, or (much worse) two different blocks were finalized at one height. |
| authoring | every validator | Some validator never produced a block. |
| state parity | all digests agree | At one finalized block, some node holds different DEX state: a consensus bug. |
| clear latency | (information) | Time from placing an order to its settlement, p50 and p99. |

A run passes only if **every** check passes.

The checks come in two groups. *One head*, *liveness*, *finality*, *authoring* and *state
parity* judge the **chain**. *Offered load*, *clearing SLO* and *SEALED zero-loss* judge the
**application**. A network can pass every chain check and fail the application ones,
and the case study's failing run did exactly that.

## The networks

| Net | Validators | What it tests |
|---|---|---|
| `lasair6` | 6 lasair | lasair alone runs the whole chain: authoring, finality, and guaranteeing every DEX round |
| `lasair-pj` | 3 lasair + 3 PolkaJam 0.1.29 | lasair and PolkaJam on one chain, each validator holding only its own key: the mixed-client case |
| `pj6` | 6 PolkaJam | the control: the DEX with no lasair node at all |

Running the same load on all three separates the causes. A failure only on `lasair6` points
at lasair doing something alone that PolkaJam covers on `lasair-pj`. A failure on `pj6` too
points at the DEX or the load.

## What a soak does not prove

- **Performance under other load.** A soak proves one load profile for one duration.
  Twice the rate is a different test.
- **Byzantine behaviour.** All validators are honest.
- **Anything after it ends.** A pass at one hour says nothing about hour two; the soak
  results note which failures only appeared late.

Next: [Running a soak](02-running-a-soak.md)
