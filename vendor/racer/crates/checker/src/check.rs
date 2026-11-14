//! Checking properties against a trace.

use crate::property::{register_name, resolve, Kind, Property};
use racer_sim::tia::{Signal, CLOCKS_PER_LINE, HBLANK_CLOCKS};
use racer_sim::trace::{Chip, Trace, VideoFrame};
use serde::Serialize;
use std::fmt;

/// The first place a property failed, and how often.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Failure {
    /// The property's name.
    pub property: String,
    /// Where it first failed: the frame, and the line and color clock where
    /// that applies.
    pub frame: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clock: Option<u16>,
    /// What was found there.
    pub observed: String,
    /// What the property requires.
    pub required: String,
    /// The number of checked frames in which the property failed.
    pub frames_failed: usize,
}

impl fmt::Display for Failure {
    /// For example: "write_window failed: frame 3, line 57: COLUBK write took
    /// effect at color clock 81; required before 68 (in 9 of 9 frames)".
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{} failed: frame {}", self.property, self.frame)?;
        if let Some(line) = self.line {
            write!(f, ", line {line}")?;
        }
        if let Some(clock) = self.clock {
            write!(f, ", clock {clock}")?;
        }
        write!(f, ": {}; required {}", self.observed, self.required)?;
        if self.frames_failed > 1 {
            write!(f, " (in {} frames)", self.frames_failed)?;
        }
        Ok(())
    }
}

/// Where a property failed within one frame.
struct Miss {
    line: Option<u32>,
    clock: Option<u16>,
    observed: String,
    required: String,
}

fn describe(s: Signal) -> String {
    match s {
        Signal::Sync => "sync".into(),
        Signal::Blank => "blank".into(),
        Signal::Color(c) => format!("color ${c:02x}"),
    }
}

/// A state property on frame `number`: the value its snapshot holds, against
/// the one it requires.
fn check_state(
    property: &Property,
    number: u64,
    line: Option<u32>,
    value: Option<u8>,
    values: &[u8],
    snapshots: &[crate::state::Snapshot],
) -> Option<Miss> {
    let required = match value {
        Some(v) => v,
        None => *values.get(number as usize - 1)?,
    };
    let (what, got) = match &property.kind {
        Kind::Ram { address, .. } => (
            format!("${address:02x}"),
            snapshots
                .iter()
                .find(|s| s.frame == number && s.line == line)
                .map(|s| s.ram[(*address & 0x7F) as usize]),
        ),
        Kind::State { register, .. } => (
            register.clone(),
            snapshots
                .iter()
                .find(|s| s.frame == number && s.line == line)
                .and_then(|s| s.values.get(register).copied()),
        ),
        _ => unreachable!("a state property"),
    };
    let when = match line {
        Some(l) => format!("at the start of line {l}"),
        None => "at the end of the frame".into(),
    };
    match got {
        Some(v) if v == required => None,
        Some(v) => Some(Miss {
            line,
            clock: None,
            observed: format!("{when}, {what} holds ${v:02x}"),
            required: format!("{what} holding ${required:02x}"),
        }),
        None => Some(Miss {
            line,
            clock: None,
            observed: format!("no state recorded {when}"),
            required: format!("{what} holding ${required:02x}"),
        }),
    }
}

/// Check `properties` against the frames of `trace` and the frame lengths
/// in `frame_lengths`, skipping the first `skip` frames. Frames are numbered
/// from 1, at the first `VSYNC`. Returns the failures, one per failing
/// property.
pub fn check(
    properties: &[Property],
    trace: &Trace,
    frame_lengths: &[u32],
    skip: usize,
    snapshots: &[crate::state::Snapshot],
) -> Vec<Failure> {
    let mut failures = Vec::new();
    for property in properties {
        let mut checked: Vec<(u64, Option<Miss>)> = Vec::new();
        let mut misses = |number: u64, miss: Option<Miss>| checked.push((number, miss));
        // The frames to check are the completed frames, numbered from 1,
        // whatever levels were traced. A property that checks no frame fails.
        if frame_lengths.len() <= skip {
            misses(
                0,
                Some(Miss {
                    line: None,
                    clock: None,
                    observed: format!("{} completed frames, all skipped", frame_lengths.len()),
                    required: "at least one checked frame".into(),
                }),
            );
        }
        for (i, &length) in frame_lengths.iter().enumerate().skip(skip) {
            let number = i as u64 + 1;
            if !property.on_frame(number) {
                continue;
            }
            let miss = match &property.kind {
                Kind::FrameLength { lines } => (length != *lines).then(|| Miss {
                    line: None,
                    clock: None,
                    observed: format!("{length} lines"),
                    required: format!("{lines} lines"),
                }),
                Kind::Ram {
                    line, value, values, ..
                }
                | Kind::State {
                    line, value, values, ..
                } => check_state(property, number, *line, *value, values, snapshots),
                _ => match trace.frames.iter().find(|f| f.number == number) {
                    Some(frame) => check_frame(property, properties, frame, trace),
                    None if property.needs_video() => Some(Miss {
                        line: None,
                        clock: None,
                        observed: "no video recorded for this frame".into(),
                        required: "the video trace".into(),
                    }),
                    None => check_frame(
                        property,
                        properties,
                        &VideoFrame {
                            number,
                            lines: Vec::new(),
                        },
                        trace,
                    ),
                },
            };
            misses(number, miss);
        }
        // A pitch over frames the run never finished is not checked at all.
        if let Kind::AudioPitch { frames, .. } = &property.kind {
            if frame_lengths.len() < frames.end as usize - 1 {
                misses(
                    u64::from(frames.end) - 1,
                    Some(Miss {
                        line: None,
                        clock: None,
                        observed: format!("{} completed frames", frame_lengths.len()),
                        required: format!("frames {frames} completed, to measure the pitch"),
                    }),
                );
            }
        }
        let (first, frames_failed) = match property.flicker() {
            Some((shown, frames)) if frame_lengths.len() > skip => flicker_miss(checked, shown, frames),
            _ => {
                let frames_failed = checked.iter().filter(|(_, m)| m.is_some()).count();
                (checked.into_iter().find_map(|(n, m)| m.map(|m| (n, m))), frames_failed)
            }
        };
        if let Some((frame, miss)) = first {
            failures.push(Failure {
                property: property.name(),
                frame,
                line: miss.line,
                clock: miss.clock,
                observed: miss.observed,
                required: miss.required,
                frames_failed,
            });
        }
    }
    failures
}

/// For an object that may flicker, given whether it was drawn on each
/// checked frame: the first run of `frames` consecutive frames that shows it
/// on fewer than `shown`, with the first frame of the run, and the number of
/// frames that hide it in any such run. A frame on which it is not drawn in
/// full counts as hidden.
fn flicker_miss(checked: Vec<(u64, Option<Miss>)>, shown: u32, frames: u32) -> (Option<(u64, Miss)>, usize) {
    let n = frames as usize;
    if checked.len() < n {
        let miss = Miss {
            line: None,
            clock: None,
            observed: format!("{} checked frames", checked.len()),
            required: format!("at least {frames} checked frames, to check {shown} of every {frames}"),
        };
        return (Some((checked.first().map_or(0, |(f, _)| *f), miss)), checked.len());
    }
    let failing: Vec<usize> = (0..checked.len().saturating_sub(n - 1))
        .filter(|&i| checked[i..i + n].iter().filter(|(_, m)| m.is_none()).count() < shown as usize)
        .collect();
    let Some(&i) = failing.first() else {
        return (None, 0);
    };
    let window = &checked[i..i + n];
    let hidden: Vec<String> = window
        .iter()
        .filter(|(_, m)| m.is_some())
        .map(|(f, _)| f.to_string())
        .collect();
    let (hidden_frame, miss) = window
        .iter()
        .find_map(|(f, m)| m.as_ref().map(|m| (*f, m)))
        .expect("a failing run hides the object at least once");
    let (a, b) = (window[0].0, window[n - 1].0);
    let miss = Miss {
        line: miss.line,
        clock: miss.clock,
        observed: format!(
            "shown on {} of frames {a}-{b}, hidden on frames {}; on frame {hidden_frame}, {}",
            n - hidden.len(),
            hidden.join(", "),
            miss.observed
        ),
        required: format!(
            "the object shown on at least {shown} of every {frames} consecutive frames, shown meaning {}",
            miss.required
        ),
    };
    let frames_failed = (0..checked.len())
        .filter(|&f| checked[f].1.is_some() && failing.iter().any(|&i| (i..i + n).contains(&f)))
        .count();
    (Some((a, miss)), frames_failed)
}

fn check_frame(property: &Property, properties: &[Property], frame: &VideoFrame, trace: &Trace) -> Option<Miss> {
    let lines = &frame.lines;
    match &property.kind {
        // Checked against the frame length, or the state, by `check`.
        Kind::FrameLength { .. } | Kind::Ram { .. } | Kind::State { .. } => None,

        Kind::SignalOnLines {
            signal,
            first_line,
            lines: count,
        } => {
            let wanted = signal.signal();
            // Every run of the signal, as (line, clock, length in clocks).
            let mut spans: Vec<(u32, u16, u32)> = Vec::new();
            let mut open = false;
            for (n, line) in lines.iter().enumerate() {
                for run in &line.runs {
                    if run.signal == wanted {
                        match (open, spans.last_mut()) {
                            (true, Some(last)) => last.2 += run.length as u32,
                            _ => spans.push((n as u32, run.start, run.length as u32)),
                        }
                        open = true;
                    } else {
                        open = false;
                    }
                }
            }
            let required = format!(
                "{} from line {first_line} for exactly {count} lines ({} clocks)",
                describe(wanted),
                count * CLOCKS_PER_LINE as u32
            );
            match spans.as_slice() {
                [] => Some(Miss {
                    line: None,
                    clock: None,
                    observed: format!("no {}", describe(wanted)),
                    required,
                }),
                [(line, clock, len), ..] if *line != *first_line || *len != count * CLOCKS_PER_LINE as u32 => {
                    Some(Miss {
                        line: Some(*line),
                        clock: Some(*clock),
                        observed: format!("{} began here and lasted {len} clocks", describe(wanted)),
                        required,
                    })
                }
                [_, (line, clock, _), ..] => Some(Miss {
                    line: Some(*line),
                    clock: Some(*clock),
                    observed: format!("{} began again here", describe(wanted)),
                    required,
                }),
                _ => None,
            }
        }

        Kind::BlankOutside { lines: span } => lines.iter().enumerate().find_map(|(n, line)| {
            if span.contains(n as u32) {
                return None;
            }
            line.runs
                .iter()
                .find(|r| matches!(r.signal, Signal::Color(_)))
                .map(|r| Miss {
                    line: Some(n as u32),
                    clock: Some(r.start),
                    observed: describe(r.signal),
                    required: format!("no color outside lines {span}"),
                })
        }),

        Kind::ConstantOutput {
            lines: span,
            clocks,
            color,
        } => region_miss(lines, span, clocks, |_| *color, &|_, _, _| false, "every clock"),

        Kind::OutputByLine {
            lines: span,
            clocks,
            start,
            step,
        } => region_miss(
            lines,
            span,
            clocks,
            |n| start.wrapping_add(step.wrapping_mul(n as u8)),
            &|_, _, _| false,
            &format!("line {} + n to show ({start} + {step} × n) mod 256", span.start),
        ),

        Kind::OutputColors {
            lines: span,
            clocks,
            colors,
        } => region_miss(
            lines,
            span,
            clocks,
            |n| colors[n as usize],
            &|_, _, _| false,
            &format!("line {} + n to show colors[n]", span.start),
        ),

        Kind::Object {
            lines: span,
            rows,
            color,
            positions,
            behind,
            ..
        } => {
            let front: Vec<&Property> = properties
                .iter()
                .filter(|p| p.name.as_ref().is_some_and(|n| behind.contains(n)))
                .collect();
            object_miss(lines, frame.number, span, rows, *color, positions, &front)
        }

        Kind::Image {
            lines: span,
            clocks,
            rows,
            behind,
        } => {
            let front: Vec<&Property> = properties
                .iter()
                .filter(|p| p.name.as_ref().is_some_and(|n| behind.contains(n)))
                .collect();
            let covered = |line: u32, clock: u32, got: Option<Signal>| {
                clock >= HBLANK_CLOCKS as u32
                    && front
                        .iter()
                        .any(|p| covers(p, frame.number, line, clock - HBLANK_CLOCKS as u32, got))
            };
            let mut first = span.start;
            rows.iter().enumerate().find_map(|(n, band)| {
                let band_lines = crate::property::Span {
                    start: first,
                    end: first + band.lines,
                };
                first = band_lines.end;
                let mut start = clocks.start;
                band.runs.iter().find_map(|&[color, len]| {
                    let run = crate::property::Span {
                        start,
                        end: start + len,
                    };
                    start = run.end;
                    region_miss(
                        lines,
                        &band_lines,
                        &run,
                        |_| color as u8,
                        &covered,
                        &format!("row {n} of the image"),
                    )
                })
            })
        }

        Kind::WriteWindow {
            chip,
            register,
            lines: span,
            clocks,
            windows,
        } => {
            let chip: Chip = (*chip).into();
            let reg = match resolve(chip, register) {
                Ok(r) => r,
                Err(e) => {
                    return Some(Miss {
                        line: None,
                        clock: None,
                        observed: e,
                        required: "a known register".into(),
                    })
                }
            };
            let name = register_name(chip, reg);
            let writes: Vec<_> = trace.writes_to(chip, reg, frame.number).collect();
            let Some(clocks) = clocks else {
                return windows_miss(&writes, span, windows, &name);
            };
            let window = if clocks.start == 0 {
                format!("before {}", clocks.end)
            } else {
                format!("at clock {} or later and before {}", clocks.start, clocks.end)
            };
            (span.start..span.end).find_map(|line| {
                let on_line: Vec<_> = writes.iter().filter(|w| w.line == line).collect();
                if on_line.is_empty() {
                    return Some(Miss {
                        line: Some(line),
                        clock: None,
                        observed: format!("no {name} write"),
                        required: format!("a {name} write taking effect {window}"),
                    });
                }
                on_line.iter().find(|w| !clocks.contains(w.clock as u32)).map(|w| Miss {
                    line: Some(line),
                    clock: Some(w.clock),
                    observed: format!("{name} write took effect at color clock {}", w.clock),
                    required: window.clone(),
                })
            })
        }

        Kind::AudioRegisters { channel, settings } => {
            let Some(want) = settings.get(frame.number as usize - 1) else {
                return Some(Miss {
                    line: None,
                    clock: None,
                    observed: format!("{} settings", settings.len()),
                    required: format!("a setting for frame {}", frame.number),
                });
            };
            // AUDC0, AUDF0, AUDV0 are $15, $17, $19; channel 1's one more.
            let names = ["AUDC", "AUDF", "AUDV"];
            let got: Vec<Option<u8>> = (0..3)
                .map(|i| {
                    let reg = 0x15 + 2 * i + channel;
                    trace
                        .register_writes
                        .iter()
                        .rev()
                        .find(|w| w.chip == Chip::Tia && w.register == reg && w.frame <= frame.number)
                        .map(|w| w.value)
                })
                .collect();
            let shown = |v: &[Option<u8>]| {
                v.iter()
                    .zip(names)
                    .map(|(v, n)| match v {
                        Some(v) => format!("{n}{channel} {v}"),
                        None => format!("{n}{channel} never written"),
                    })
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            let wanted: Vec<Option<u8>> = want.iter().map(|&v| Some(v)).collect();
            // Only the bits the TIA uses: 4, 5, and 4 (TIA-AUDIO-1).
            let masked: Vec<Option<u8>> = got
                .iter()
                .zip([0x0F, 0x1F, 0x0F])
                .map(|(v, m)| v.map(|v| v & m))
                .collect();
            (masked != wanted).then(|| Miss {
                line: None,
                clock: None,
                observed: format!("at the end of the frame, {}", shown(&masked)),
                required: shown(&wanted),
            })
        }

        Kind::AudioPitch {
            channel,
            frames,
            hz,
            cents,
            clock_hz,
        } => {
            if frame.number != u64::from(frames.end) - 1 {
                return None;
            }
            let Some(samples) = trace.audio_of_frames(u64::from(frames.start), u64::from(frames.end)) else {
                return Some(Miss {
                    line: None,
                    clock: None,
                    observed: "no audio recorded for these frames".into(),
                    required: format!("the audio trace of frames {frames}"),
                });
            };
            let levels: Vec<u8> = samples.iter().map(|s| s[*channel as usize]).collect();
            let required = format!("channel {channel} at {hz:.2} Hz, within {cents} cents, over frames {frames}");
            if levels.iter().all(|&l| l == 0) {
                return Some(Miss {
                    line: None,
                    clock: None,
                    observed: "silence".into(),
                    required,
                });
            }
            let period = (1..=levels.len() / 2).find(|&p| levels.iter().zip(&levels[p..]).all(|(a, b)| a == b));
            let Some(period) = period else {
                return Some(Miss {
                    line: None,
                    clock: None,
                    observed: format!("no repeating pattern in {} samples", levels.len()),
                    required,
                });
            };
            let got = clock_hz / period as f64;
            let off = 1200.0 * (got / hz).log2();
            (off.abs() > *cents).then(|| Miss {
                line: None,
                clock: None,
                observed: format!("{got:.2} Hz, repeating every {period} audio clocks, {off:+.1} cents"),
                required,
            })
        }
    }
}

/// The first set bit of an object's rows that does not show its color, on
/// frame `number`, nor the color of an object in `front` whose set bit
/// covers it on that frame.
fn object_miss(
    lines: &[racer_sim::trace::VideoLine],
    number: u64,
    span: &crate::property::Span,
    rows: &[u8],
    color: u8,
    positions: &[[u32; 2]],
    front: &[&Property],
) -> Option<Miss> {
    let Some(&[x, y]) = positions.get(number as usize - 1) else {
        return Some(Miss {
            line: None,
            clock: None,
            observed: format!("no position for frame {number}"),
            required: format!("a position for each checked frame ({} given)", positions.len()),
        });
    };
    let want = Signal::Color(color);
    for (r, row) in rows.iter().enumerate() {
        let line = span.start + y + r as u32;
        if !span.contains(line) {
            continue;
        }
        let video = lines.get(line as usize);
        for bit in 0..8u32 {
            let visible = x + bit;
            if row & (0x80 >> bit) == 0 || visible >= 160 {
                continue;
            }
            let clock = (HBLANK_CLOCKS as u32 + visible) as u16;
            let got = video.and_then(|v| v.signal_at(clock));
            if got != Some(want) && !front.iter().any(|p| covers(p, number, line, visible, got)) {
                return Some(Miss {
                    line: Some(line),
                    clock: Some(clock),
                    observed: got.map(describe).unwrap_or_else(|| "nothing".into()),
                    required: format!(
                        "{} (row {r}, bit {}, of the object at visible clock {x}, picture line {y})",
                        describe(want),
                        7 - bit
                    ),
                });
            }
        }
    }
    None
}

/// Whether object property `p`, on frame `number`, has a set bit at frame
/// line `line`, visible clock `visible`, and `got` is its color.
fn covers(p: &Property, number: u64, line: u32, visible: u32, got: Option<Signal>) -> bool {
    let Kind::Object {
        lines: span,
        rows,
        color,
        positions,
        ..
    } = &p.kind
    else {
        return false;
    };
    let Some(&[x, y]) = positions.get(number as usize - 1) else {
        return false;
    };
    let (Some(r), Some(bit)) = (line.checked_sub(span.start + y), visible.checked_sub(x)) else {
        return false;
    };
    got == Some(Signal::Color(*color)) && bit < 8 && rows.get(r as usize).is_some_and(|row| row & (0x80 >> bit) != 0)
}

/// The first clock in a region whose color is not `expected(n)` for line
/// `span.start + n`.
/// The first line in `span` whose writes do not land one in each of
/// `windows`, in order: `write_window` with `windows`.
fn windows_miss(
    writes: &[&racer_sim::trace::RegisterWrite],
    span: &crate::property::Span,
    windows: &[crate::property::Span],
    name: &str,
) -> Option<Miss> {
    let required = format!(
        "one {name} write taking effect in each of clocks {}, in order",
        windows
            .iter()
            .map(|w| format!("{}-{}", w.start, w.end - 1))
            .collect::<Vec<_>>()
            .join(", ")
    );
    (span.start..span.end).find_map(|line| {
        let on_line: Vec<_> = writes.iter().filter(|w| w.line == line).collect();
        let at: Vec<String> = on_line.iter().map(|w| w.clock.to_string()).collect();
        let observed = match at.as_slice() {
            [] => format!("no {name} write"),
            [one] => format!("one {name} write, taking effect at color clock {one}"),
            _ => format!("{name} writes taking effect at color clocks {}", at.join(", ")),
        };
        let fits = on_line.len() == windows.len()
            && on_line
                .iter()
                .zip(windows)
                .all(|(w, window)| window.contains(w.clock as u32));
        (!fits).then(|| Miss {
            line: Some(line),
            clock: on_line
                .iter()
                .zip(windows)
                .find(|(w, window)| !window.contains(w.clock as u32))
                .map(|(w, _)| w.clock),
            observed,
            required: required.clone(),
        })
    })
}

fn region_miss(
    lines: &[racer_sim::trace::VideoLine],
    span: &crate::property::Span,
    clocks: &crate::property::Span,
    expected: impl Fn(u32) -> u8,
    covered: &dyn Fn(u32, u32, Option<Signal>) -> bool,
    rule: &str,
) -> Option<Miss> {
    for line in span.start..span.end {
        let n = line - span.start;
        let want = Signal::Color(expected(n));
        let Some(video) = lines.get(line as usize) else {
            return Some(Miss {
                line: Some(line),
                clock: None,
                observed: "the frame ended before this line".into(),
                required: format!("{rule} in lines {span}"),
            });
        };
        for clock in clocks.start..clocks.end {
            let got = video.signal_at(clock as u16);
            if got != Some(want) && !covered(line, clock, got) {
                return Some(Miss {
                    line: Some(line),
                    clock: Some(clock as u16),
                    observed: got.map(describe).unwrap_or_else(|| "nothing".into()),
                    required: format!("{} ({rule}, clocks {clocks})", describe(want)),
                });
            }
        }
    }
    None
}
