# What cross-client telemetry reveals

## What one client cannot see about itself

lasair's metrics say what lasair did: how many guarantees it distributed over CE-135, how
many streams it opened of each kind, how many failed *on its side*. They cannot say how the
receiving peer judged what arrived. If a peer quietly rejects a guarantee lasair sent, or
drops a stream lasair opened, lasair's counters show a successful send.

The receiving peer knows. And JIP-3 gives it a way to say so: event 28, **peer
misbehaved**, carries the peer and a free-text reason. When PolkaJam reports JIP-3
telemetry into the stack, every judgement it makes about its peers lands in Loki.

## The query

```logql
{net="lasair-pj", source="jip3"} | json | event="peer_misbehaved"
```

And to count them by node and reason:

```logql
sum by (node, reason) (count_over_time({net="lasair-pj", source="jip3"} | json | event="peer_misbehaved" [1h]))
```

Or in the **Logs** dashboard: pick the `lasair-pj` run and type `peer_misbehaved` into the
search box.

## What it found

On the `lasair-pj` run `lasair-pj-20260928T184439Z`, all three PolkaJam nodes reported
peers misbehaving, for two reasons. Real lines, one of each, from `pj3`:

```json
{"event":"peer_misbehaved","id":3301,"ts":"2026-09-28T18:50:26.275860Z","peer":"4418fb8c85bb3985","reason":"Invalid guarantee: No signature from peer eecgwpgwq3noky4ijm4jmvjtmuzv44qvigciusxakq5epnrfj2utb"}
{"event":"peer_misbehaved","id":6978,"ts":"2026-09-28T18:54:08.141433Z","peer":"ad93247bd0130755","reason":"Bad incoming stream protocol 153"}
```

(`peer` is the peer being reported, its key cut to 16 hex digits by the receiver; the id
in the reason text is PolkaJam's own encoding of a peer id.)

### Finding 1: "Invalid guarantee: No signature from peer"

PolkaJam rejected guarantees distributed to it over **CE-135** (guarantee distribution),
and its reason names a peer whose signature it did not find in the guarantee. The same
reason appears on `pj3`, `pj4` and `pj5` within a millisecond of each other, so it is not
one node's quirk: the guarantee, as sent, did not satisfy PolkaJam's check. lasair's own
CE-135 counters count a guarantee when it is sent; they cannot see this.

### Finding 2: "Bad incoming stream protocol 153"

PolkaJam refused a stream of kind **153** that a lasair peer opened. Stream kinds are
JAMNP-S protocol numbers; lasair's networking notes place 153 among the stream kinds it
uses for GRANDPA messages from a draft revision of the networking protocol (CE 149 to 153).
PolkaJam does not accept that kind and treats it as misbehaviour; lasair's notes record
that this PolkaJam version can close the whole connection a stream like that arrives on.
The same class of problem was found before with another stream kind
(`Bad incoming stream protocol 192`, a lasair-only stream) and fixed in lasair by removing
that stream.

Both were recorded as **lasair interop problems**: places where lasair and PolkaJam do not
yet speak exactly the same protocol, invisible in lasair's own metrics and visible in one
query against PolkaJam's telemetry.

## Reading a peer's complaint

A `peer_misbehaved` event is a **lead, not a verdict**. It says one implementation
disagreed with another. Which one is right is decided by the specifications, the Gray
Paper and JAMNP-S, not by either client. So:

1. **Collect the evidence**: the events (who reported, which peer, when, how often), and
   what the accused node logged at the same moment. The Logs dashboard puts both in one
   timeline: pick the run, all nodes, and search for the timestamp's minute.
2. **Find the rule**: which part of the spec the reason refers to (CE-135's guarantee
   format and signatures; the list of stream kinds).
3. **Decide who deviates**, and fix it there. Sometimes it is the complaining side.

## Why this is worth the setup

Without cross-client telemetry, both findings would have surfaced, if at all, as vague
symptoms: guarantees that land less often than expected, connections that churn. With it,
each one arrived with a reason string, a count and a timestamp. That is the practical
payoff of JIP-3 for a mixed network: every client becomes an auditor of every other.

Next: [Adding another client](03-adding-another-client.md)
