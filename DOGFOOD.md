# DOGFOOD — brogue

_Session: 2026-04-23T13:10:43, driver: pty, duration: 3.0 min_

**PASS** — ran for 2.0m, captured 10 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found no findings worth flagging. Game reached 79 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`.

## Findings

### Blockers

_None._

### Majors

_None._

### Minors

_None._

### Nits

_None._

### UX (feel-better-ifs)

_None._

## Coverage

- Driver backend: `pty`
- Keys pressed: 1112 (unique: 67)
- State samples: 89 (unique: 79)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=83.2, B=21.6, C=18.1
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/brogue-20260423-130838`

Unique keys exercised: +, ,, -, ., /, 0, 1, 2, 3, 4, 5, :, ;, =, ?, H, R, [, ], a, b, backspace, c, comma, ctrl+l, d, delete, down, e, end, enter, escape, f1, f2, greater_than_sign, h, home, i, j, k ...

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.4 | 0.0 | `brogue-20260423-130838/milestones/first_input.txt` | key=enter |
