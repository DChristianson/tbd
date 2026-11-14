//! Properties: what a trace must satisfy, as written in a check file.
//!
//! Ranges are half-open, `[start, end)`, as in the design sketches: lines
//! `[40, 232]` are lines 40 through 231. Line numbers are within a frame
//! (`TIA-FRAME-1`); color clocks are within a line, 0-227, where 68 is the
//! first visible clock.

use racer_sim::tia::Signal;
use racer_sim::trace::Chip;
use serde::{Deserialize, Serialize};
use std::fmt;

/// A half-open range `[start, end)`, written as a two-element array.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(from = "[u32; 2]", into = "[u32; 2]")]
pub struct Span {
    pub start: u32,
    pub end: u32,
}

impl From<[u32; 2]> for Span {
    fn from([start, end]: [u32; 2]) -> Span {
        Span { start, end }
    }
}

impl From<Span> for [u32; 2] {
    fn from(s: Span) -> [u32; 2] {
        [s.start, s.end]
    }
}

impl Span {
    pub fn contains(&self, v: u32) -> bool {
        v >= self.start && v < self.end
    }
}

impl fmt::Display for Span {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        if self.end == self.start + 1 {
            write!(f, "{}", self.start)
        } else {
            write!(f, "{}-{}", self.start, self.end - 1)
        }
    }
}

/// A signal that is not a color, as named in check files.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SignalName {
    Sync,
    Blank,
}

impl SignalName {
    /// Its name in a check file.
    pub fn name(self) -> &'static str {
        match self {
            SignalName::Sync => "sync",
            SignalName::Blank => "blank",
        }
    }

    pub fn signal(self) -> Signal {
        match self {
            SignalName::Sync => Signal::Sync,
            SignalName::Blank => Signal::Blank,
        }
    }
}

/// A register, by name (`"COLUBK"`) or number.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Register {
    Name(String),
    Number(u8),
}

/// TIA write registers, by address modulo 64.
#[rustfmt::skip]
pub const TIA_REGISTERS: [&str; 45] = [
    "VSYNC", "VBLANK", "WSYNC", "RSYNC", "NUSIZ0", "NUSIZ1", "COLUP0", "COLUP1", "COLUPF", "COLUBK", "CTRLPF",
    "REFP0", "REFP1", "PF0", "PF1", "PF2", "RESP0", "RESP1", "RESM0", "RESM1", "RESBL", "AUDC0", "AUDC1", "AUDF0",
    "AUDF1", "AUDV0", "AUDV1", "GRP0", "GRP1", "ENAM0", "ENAM1", "ENABL", "HMP0", "HMP1", "HMM0", "HMM1", "HMBL",
    "VDELP0", "VDELP1", "VDELBL", "RESMP0", "RESMP1", "HMOVE", "HMCLR", "CXCLR",
];

/// RIOT write registers, by address modulo 32.
#[rustfmt::skip]
const RIOT_REGISTERS: [(&str, u8); 8] = [
    ("SWCHA", 0x00), ("SWACNT", 0x01), ("SWCHB", 0x02), ("SWBCNT", 0x03),
    ("TIM1T", 0x14), ("TIM8T", 0x15), ("TIM64T", 0x16), ("T1024T", 0x17),
];

/// The chip and register number a register name refers to.
pub fn resolve(chip: Chip, register: &Register) -> Result<u8, String> {
    match register {
        Register::Number(n) => Ok(*n),
        Register::Name(name) => {
            let upper = name.to_uppercase();
            let found = match chip {
                Chip::Tia => TIA_REGISTERS.iter().position(|r| *r == upper).map(|i| i as u8),
                Chip::Riot => RIOT_REGISTERS.iter().find(|(r, _)| *r == upper).map(|(_, n)| *n),
            };
            found.ok_or_else(|| format!("unknown {chip:?} register {name:?}"))
        }
    }
}

/// The name of a register, for messages.
pub fn register_name(chip: Chip, register: u8) -> String {
    let name = match chip {
        Chip::Tia => TIA_REGISTERS.get(register as usize).copied(),
        Chip::Riot => RIOT_REGISTERS.iter().find(|(_, n)| *n == register).map(|(r, _)| *r),
    };
    name.map(String::from)
        .unwrap_or_else(|| format!("register ${register:02x}"))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChipName {
    Tia,
    Riot,
}

impl ChipName {
    /// Its name in a check file.
    pub fn name(self) -> &'static str {
        match self {
            ChipName::Tia => "tia",
            ChipName::Riot => "riot",
        }
    }
}

impl From<ChipName> for Chip {
    fn from(c: ChipName) -> Chip {
        match c {
            ChipName::Tia => Chip::Tia,
            ChipName::Riot => Chip::Riot,
        }
    }
}

/// What a trace must satisfy, in every checked frame.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Kind {
    /// The frame is exactly `lines` lines long.
    FrameLength { lines: u32 },
    /// `signal` is output as one unbroken span, beginning on line
    /// `first_line` and lasting exactly `lines` lines' worth of clocks
    /// (`lines` × 228). For `VSYNC`, this is how long sync lasts, wherever in
    /// the line it starts.
    SignalOnLines {
        signal: SignalName,
        first_line: u32,
        lines: u32,
    },
    /// No color is output on any line outside `lines`.
    BlankOutside { lines: Span },
    /// Every clock in `clocks` of every line in `lines` shows `color`.
    ConstantOutput { lines: Span, clocks: Span, color: u8 },
    /// Every clock in `clocks` of line `lines.start + n` shows
    /// `(start + step × n) mod 256`.
    OutputByLine {
        lines: Span,
        clocks: Span,
        start: u8,
        step: u8,
    },
    /// Every clock in `clocks` of line `lines.start + n` shows `colors[n]`:
    /// any color for each line, one per line in `lines`.
    OutputColors { lines: Span, clocks: Span, colors: Vec<u8> },
    /// On every line in `lines`, `register` is written at least once, and
    /// every write to it takes effect at a clock within `clocks`; or, with
    /// `windows` in place of `clocks`, it is written exactly once in each
    /// window, in order, and nowhere else: for a register written more than
    /// once on a line, such as a playfield register rewritten for the right
    /// half (milestone 6).
    WriteWindow {
        chip: ChipName,
        register: Register,
        lines: Span,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        clocks: Option<Span>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        windows: Vec<Span>,
    },
    /// An image (milestone 6): over `lines` by `clocks`, each band of
    /// `rows` covers the next `lines` lines, and each of its runs, a color
    /// and a length in clocks, the next clocks of every one of them. The
    /// bands' lines add up to `lines`, and each band's runs to `clocks`.
    /// `behind` names the object properties drawn in front of it
    /// (milestone 7): a clock one of their set bits covers, on the same
    /// frame, may show that object's color instead.
    Image {
        lines: Span,
        clocks: Span,
        rows: Vec<Band>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        behind: Vec<String>,
    },
    /// An object, at a position that may change from frame to frame
    /// (milestone 4; decision 0010). On frame `n`, it is at `[x, y] =
    /// positions[n - 1]`: its row `r` is drawn on frame line `lines.start`
    /// plus `y` plus `r`, its bits from visible clock `x`, bit 7 leftmost,
    /// one clock each, and every set bit shows `color`. Rows that fall
    /// outside `lines`, and bits past the last visible clock, are not
    /// checked. With `shown` and `frames` (milestone 7), the object may
    /// flicker: it need be drawn, in full, on only `shown` of every
    /// `frames` consecutive checked frames. `behind` names the object
    /// properties drawn in front of it (milestone 7): a bit that one of
    /// their set bits covers, on the same frame, may show that object's
    /// color instead.
    Object {
        lines: Span,
        rows: Vec<u8>,
        color: u8,
        positions: Vec<[u32; 2]>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        shown: Option<u32>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        frames: Option<u32>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        behind: Vec<String>,
    },
    /// An audio channel's registers (milestone 9): at the end of frame `n`,
    /// channel `channel`'s `AUDC`, `AUDF`, and `AUDV`, as last written, are
    /// `settings[n - 1]`. A sound starting, changing, and stopping is its
    /// registers changing from one frame to the next; a register never yet
    /// written fails.
    AudioRegisters { channel: u8, settings: Vec<[u8; 3]> },
    /// An audio channel's pitch (milestone 9): over `frames`, channel
    /// `channel`'s output repeats with a period that, at `clock_hz` audio
    /// clocks a second, is within `cents` of `hz`. Checked once, on the last
    /// of `frames`. Silence, or an output that does not repeat, fails.
    AudioPitch {
        channel: u8,
        frames: Span,
        hz: f64,
        cents: f64,
        clock_hz: f64,
    },
    /// A byte of RAM (milestone 9, task 13): on frame `n`, at the start of
    /// `line`, or at the frame's end without one, `address` (`$80`-`$FF`)
    /// holds `values[n - 1]`, or `value` on every checked frame.
    Ram {
        address: u16,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        line: Option<u32>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        value: Option<u8>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        values: Vec<u8>,
    },
    /// A register's or counter's value (milestone 9, task 13), named as
    /// [`crate::state::names`] lists them, at the same moments as `ram`'s.
    State {
        register: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        line: Option<u32>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        value: Option<u8>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        values: Vec<u8>,
    },
}

impl Kind {
    /// Its name in a check file.
    pub fn name(&self) -> &'static str {
        match self {
            Kind::FrameLength { .. } => "frame_length",
            Kind::SignalOnLines { .. } => "signal_on_lines",
            Kind::BlankOutside { .. } => "blank_outside",
            Kind::ConstantOutput { .. } => "constant_output",
            Kind::OutputByLine { .. } => "output_by_line",
            Kind::OutputColors { .. } => "output_colors",
            Kind::WriteWindow { .. } => "write_window",
            Kind::Image { .. } => "image",
            Kind::Object { .. } => "object",
            Kind::AudioRegisters { .. } => "audio_registers",
            Kind::AudioPitch { .. } => "audio_pitch",
            Kind::Ram { .. } => "ram",
            Kind::State { .. } => "state",
        }
    }
}

/// Lines of an image that are alike: `lines` lines, each showing `runs`,
/// one `[color, length]` after another from the image's first clock.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Band {
    pub lines: u32,
    pub runs: Vec<[u32; 2]>,
}

/// A property: a kind, with an optional name used in reports and in
/// `expect_failures`. Without a name, the kind's name is used. With
/// `on_frames`, it is checked only on the frames in those ranges, such as
/// the frames on which a screen shows (milestone 9).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Property {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub on_frames: Vec<Span>,
    #[serde(flatten)]
    pub kind: Kind,
}

impl Property {
    pub fn name(&self) -> String {
        self.name.clone().unwrap_or_else(|| self.kind.name().to_string())
    }

    /// A mistake in the property that the file's syntax cannot rule out.
    pub fn validate(&self) -> Result<(), String> {
        match &self.kind {
            Kind::OutputColors { lines, colors, .. }
                if colors.len() as u32 != lines.end.saturating_sub(lines.start) =>
            {
                Err(format!(
                    "{}: {} colors for the {} lines {lines}; give one color for each line",
                    self.name(),
                    colors.len(),
                    lines.end.saturating_sub(lines.start)
                ))
            }
            Kind::WriteWindow { clocks, windows, .. } => match (clocks, windows.is_empty()) {
                (Some(_), true) | (None, false) => Ok(()),
                (Some(_), false) => Err(format!("{}: give clocks or windows, not both", self.name())),
                (None, true) => Err(format!("{}: give clocks, or windows", self.name())),
            },
            Kind::Image {
                lines, clocks, rows, ..
            } => {
                let height: u32 = rows.iter().map(|b| b.lines).sum();
                if height != lines.end.saturating_sub(lines.start) {
                    return Err(format!(
                        "{}: the rows cover {height} lines, and lines {lines} are {}",
                        self.name(),
                        lines.end.saturating_sub(lines.start)
                    ));
                }
                let width = clocks.end.saturating_sub(clocks.start);
                for (n, band) in rows.iter().enumerate() {
                    let covered: u32 = band.runs.iter().map(|[_, len]| len).sum();
                    if covered != width {
                        return Err(format!(
                            "{}: row {n}'s runs cover {covered} clocks, and clocks {clocks} are {width}",
                            self.name()
                        ));
                    }
                    if let Some([color, _]) = band.runs.iter().find(|[c, _]| *c > 0xFF) {
                        return Err(format!("{}: row {n}: {color} is not a color", self.name()));
                    }
                }
                Ok(())
            }
            Kind::AudioRegisters { channel, .. } | Kind::AudioPitch { channel, .. } if *channel > 1 => {
                Err(format!("{}: channel {channel}; give 0 or 1", self.name()))
            }
            Kind::AudioPitch {
                frames,
                hz,
                cents,
                clock_hz,
                ..
            } if frames.start == 0 || frames.end <= frames.start || *hz <= 0.0 || *cents < 0.0 || *clock_hz <= 0.0 => {
                Err(format!(
                    "{}: give frames from 1, a frequency and a clock above 0, and cents of at least 0",
                    self.name()
                ))
            }
            Kind::Ram { address, .. } if !(0x80..=0xFF).contains(address) => {
                Err(format!("{}: address ${address:04x}; RAM is $80-$FF", self.name()))
            }
            Kind::State { register, .. } if !crate::state::names().iter().any(|n| n == register) => Err(format!(
                "{}: unknown register or counter {register:?}; give one of {}",
                self.name(),
                crate::state::names().join(", ")
            )),
            Kind::Ram { value, values, .. } | Kind::State { value, values, .. }
                if value.is_some() == !values.is_empty() =>
            {
                Err(format!("{}: give value, or values, one of them", self.name()))
            }
            Kind::Object { shown, frames, .. } => match (shown, frames) {
                (None, None) => Ok(()),
                (Some(m), Some(n)) if 1 <= *m && m <= n => Ok(()),
                (Some(m), Some(n)) => Err(format!(
                    "{}: shown on {m} of every {n} frames; give 1 <= shown <= frames",
                    self.name()
                )),
                _ => Err(format!("{}: give shown and frames together", self.name())),
            },
            _ => Ok(()),
        }
    }

    /// A mistake in how the properties refer to each other: an object
    /// `behind` a name that is not another object property in `all`.
    pub fn validate_references(&self, all: &[Property]) -> Result<(), String> {
        let (Kind::Object { behind, .. } | Kind::Image { behind, .. }) = &self.kind else {
            return Ok(());
        };
        for name in behind {
            let found = all
                .iter()
                .any(|p| p.name.as_deref() == Some(name) && matches!(p.kind, Kind::Object { .. }));
            if !found || self.name.as_deref() == Some(name) {
                return Err(format!(
                    "{}: behind \"{name}\", which is not another object property",
                    self.name()
                ));
            }
        }
        Ok(())
    }

    /// For an object that may flicker, the frames it must be shown on, as
    /// `(shown, frames)`: at least `shown` of every `frames` consecutive.
    pub fn flicker(&self) -> Option<(u32, u32)> {
        match self.kind {
            Kind::Object {
                shown: Some(m),
                frames: Some(n),
                ..
            } => Some((m, n)),
            _ => None,
        }
    }

    /// Whether checking this property needs the video trace.
    pub fn needs_video(&self) -> bool {
        matches!(
            self.kind,
            Kind::SignalOnLines { .. }
                | Kind::BlankOutside { .. }
                | Kind::ConstantOutput { .. }
                | Kind::OutputByLine { .. }
                | Kind::OutputColors { .. }
                | Kind::Image { .. }
                | Kind::Object { .. }
        )
    }

    /// Whether checking this property needs the register-write trace.
    pub fn needs_writes(&self) -> bool {
        matches!(self.kind, Kind::WriteWindow { .. } | Kind::AudioRegisters { .. })
    }

    /// Whether checking this property needs the audio trace.
    pub fn needs_audio(&self) -> bool {
        matches!(self.kind, Kind::AudioPitch { .. })
    }

    /// The line a state property is read at the start of, or `None` for
    /// the frame's end; `None` for other properties too.
    pub fn state_line(&self) -> Option<Option<u32>> {
        match self.kind {
            Kind::Ram { line, .. } | Kind::State { line, .. } => Some(line),
            _ => None,
        }
    }

    /// Whether it is checked on frame `n`.
    pub fn on_frame(&self, n: u64) -> bool {
        self.on_frames.is_empty() || self.on_frames.iter().any(|s| s.contains(n as u32))
    }
}
