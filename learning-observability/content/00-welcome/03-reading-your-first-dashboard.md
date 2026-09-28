# Reading your first dashboard

Time to read a real dashboard, one panel at a time. The dashboard is **Chain health**; the
run is `lasair6-20260928T173608Z`, a one-hour soak test of six lasair validators that
passed. You will learn what a soak test is in track 4. For now: a network ran under steady
load for an hour, and at the end a checker judged it.

## Open it

On the machine that recorded this run (see lesson 0.4 if you are not sure), from the
observability checkout:

```sh
./obs link lasair6-20260928T173608Z -d chain
```

It prints the link, with the network, the run and the time range already filled in:

```text
http://localhost:3300/d/obs-chain?orgId=1&var-run_id=lasair6-20260928T173608Z&var-net=lasair6&from=1790616908817&to=1790620963000
```

(If the link ends in `from=now-6h&to=now` instead, your machine does not have this run's
record yet; lesson 0.4, *The recorded runs*, says where it is.)

## The top bar: which network, which run, which time

```text
 net [lasair6 ▾]   run_id [lasair6-20260928T173608Z ▾]        ⏱ 17:35:08 → 18:42:43 UTC  ⟳
```

- **net** and **run_id** choose whose data you see. Every panel below filters on them.
- The **time range** runs from a minute before the run began to a minute after it ended.
  Grafana shows times in your browser's time zone: in central Europe in summer (UTC+2) that
  reads 19:35 → 20:42. This course writes times in UTC.

## The question

The first panel is text. It says what the dashboard is for:

> **Is the net one chain that keeps growing and finalizing, on every node?**

Everything below is evidence for or against that one question.

## The five verdicts

```text
 ┌───────────────┬───────────────┬───────────────┬───────────────┬───────────────┐
 │ Nodes stopped │ One head      │ Finality lag, │ Finalized in  │ Finality      │
 │ during the run│ PASS = all on │ worst node    │ 5 min, slowest│ conflicts     │
 │ PASS = 0      │ one block     │ PASS ≤ 12     │ PASS > 0      │ PASS = 0      │
 │               │               │               │               │               │
 │   0  (green)  │ PASS (green)  │   0  (green)  │  71  (green)  │   0  (green)  │
 └───────────────┴───────────────┴───────────────┴───────────────┴───────────────┘
```

Each is a **stat** panel: one number describing the run (its last value at the run's end,
once it has one, not literally the range's last moment). Its title says what passes; the
colour says whether it did. Read them left to right:

- **One head: PASS.** Every node held the same block, compared by its hash.
- **Finality lag, worst node: 0.** No node's finalized block was behind its best block at
  the end. (Over the hour it never exceeded 3 slots; one epoch, 12, is the limit.)
- **Finalized in 5 min: 71.** The slowest node's finalized block moved 71 slots in the
  five minutes before the run ended: finality kept going.
- **Finality conflicts: 0.** Never two different blocks finalized at one height.
- **Nodes stopped during the run: 0, green.** All six nodes were still advancing when the
  run ended; the tile only counts a node that falls silent while the rest of the net keeps
  going. Lesson 3.6 is about reading this tile and the ones like it.

## The graphs

**Best slot per node**: six lines, one per node, climbing from slot 2 to slot 1046. They
lie exactly on top of each other, so you see what looks like one line. That is the point:
all six nodes agree on the head at every moment. A node falling behind would peel away
below the others.

**Finalized slot per node**: the same shape, climbing in small steps just under the best
slot. Finality follows the head.

**Finality lag per node**: small numbers, 0 to 3, far below the dashed red line at 12 (one
epoch). A dashed line is a threshold: trouble would be a line climbing toward it.

**Head agreement**: *distinct heads* and *distinct finalized blocks* both flat at 1 for the
whole hour, and the spread of best slots 0 or 1. One chain.

**Height per node** is **empty**, and that is fine: it reads `jam_best_height`, which
this older lasair build did not export yet. An empty panel means "no data for this query",
not "zero". Lesson 3.1 lists the usual reasons.

**Peers per node** shows only *nodes answering JIP-2*: 6. The same reason: this build did
not export `jam_peers`.

Hover over any graph: a vertical line follows your mouse across **every** panel, and a
tooltip lists each series' value at that moment. Use it to line up events across panels.

## The marks

Vertical marks cross every graph. These are **annotations**, written by the tools that ran
the soak. Hover over one to read it. On this run:

| Time (UTC) | Colour | Text |
|---|---|---|
| 17:36:09 | blue | `lasair6 up: validators lasair,lasair,lasair,lasair,lasair,lasair; lasair lasair:perf-8c62dd5` |
| 17:38:10 | blue | `soak start: 3600 s load + 180 s drain` |
| 17:38:12 | blue | `soak: load on` |
| 18:38:22 | blue | `soak: load off, draining 180 s (offered 1440, refused 0, busy 0)` |
| 18:41:06 | blue | `soak: drain done` |
| 18:41:11 to 18:41:13 | green | one per check: `clearing SLO: 1.000000 (target 0.9999) PASS`, ..., `VERDICT (orders + chain): PASS` |
| 17:38:10 to 18:41:13 | green region | `soak PASS` |
| 18:41:42 | blue | `lasair6 down` |

Now look at where the graphs end: every line stops at about 18:41:26, just before the
`lasair6 down` mark. The network was switched off, on purpose, after the soak passed, and
every node was still advancing right up to that moment: none of them fell behind the
others first. That is why *Nodes stopped during the run* reads 0, even though the time
range runs a minute past the end of the run and no node is reporting any more by its last
moment. A normal end, not a failure — and the dashboard says so without you having to
work it out from the graphs.

## Reading any dashboard, in five steps

1. **The question**: read the text panel.
2. **Whose data, when**: check net, run and time range.
3. **The verdicts**: read the stats' colours, then their numbers.
4. **The shape**: follow the lines; look for the one that differs from the others.
5. **The marks**: line up what changed with what the tools say happened.

You have just read a dashboard. Everything else in this course is more of the same, on
other questions.

Next: [How to use this course](04-how-to-use-this-course.md)
