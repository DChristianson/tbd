//! Traces: what happened, recorded at up to three levels that can be switched
//! on separately, because a full trace is large (3.6 million color clocks per
//! second of emulated time).
//!
//! - **CPU cycles:** every cycle's bus access, or that the CPU was halted,
//!   with the instruction it belongs to.
//! - **Register writes:** every write to the TIA or the RIOT, with the CPU
//!   cycle and the frame, line, and color clock at which it takes effect.
//! - **Video:** the TIA's output for every color clock, stored per line as
//!   runs of identical signals. Runs are lossless, so the same data gives
//!   each clock's signal or the compact per-line form.
//! - **Audio:** each audio clock's level on each channel, two a line.
//!
//! The checker reads only the levels its properties need.

use crate::audio::Sample;
use crate::tia::{Signal, CLOCKS_PER_LINE};

/// Which levels to record.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TraceConfig {
    pub cpu_cycles: bool,
    pub register_writes: bool,
    pub video: bool,
    pub audio: bool,
}

impl TraceConfig {
    pub const OFF: TraceConfig = TraceConfig {
        cpu_cycles: false,
        register_writes: false,
        video: false,
        audio: false,
    };
    pub const ALL: TraceConfig = TraceConfig {
        cpu_cycles: true,
        register_writes: true,
        video: true,
        audio: true,
    };
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Access {
    Read,
    Write,
    /// The CPU was halted (RDY, after `WSYNC`): no bus access this cycle.
    Halted,
}

/// The instruction a cycle belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Instruction {
    /// The reset sequence.
    Reset,
    /// The instruction whose opcode is at `pc`.
    At { pc: u16, opcode: u8 },
}

/// One CPU cycle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CpuCycle {
    /// Cycles since power-on; this cycle's number.
    pub cycle: u64,
    pub access: Access,
    /// The address on the bus, as the CPU drove it (all 16 bits).
    pub addr: u16,
    /// The value read or written.
    pub value: u8,
    pub instruction: Instruction,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Chip {
    Tia,
    Riot,
}

/// A write to a TIA or RIOT register.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RegisterWrite {
    pub chip: Chip,
    /// The register: for the TIA, the address modulo 64; for the RIOT, the
    /// address modulo 32 (so `TIM64T` is $16).
    pub register: u8,
    pub value: u8,
    /// The CPU cycle that performed the write.
    pub cycle: u64,
    /// Where the write takes effect: the frame (0 before the first `VSYNC`),
    /// line, and color clock. For the TIA this is the first clock after the
    /// writing cycle (`TIA-WRITE-1`).
    pub frame: u64,
    pub line: u32,
    pub clock: u16,
}

/// A span of identical output within a line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Run {
    /// The first color clock of the run.
    pub start: u16,
    pub length: u16,
    pub signal: Signal,
}

/// One line of video, as runs covering clocks 0-227 in order. A line already
/// in progress when recording began starts at the clock recording began.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct VideoLine {
    pub runs: Vec<Run>,
}

impl VideoLine {
    fn push(&mut self, clock: u16, signal: Signal) {
        if let Some(last) = self.runs.last_mut() {
            if last.signal == signal && last.start + last.length == clock {
                last.length += 1;
                return;
            }
        }
        self.runs.push(Run {
            start: clock,
            length: 1,
            signal,
        });
    }

    /// The signal at `clock`, if the line reached it.
    pub fn signal_at(&self, clock: u16) -> Option<Signal> {
        self.runs
            .iter()
            .find(|r| clock >= r.start && clock < r.start + r.length)
            .map(|r| r.signal)
    }

    /// Every recorded clock's signal, from the line's first recorded run:
    /// all 228 clocks, except on a line in progress when recording began.
    pub fn signals(&self) -> Vec<Signal> {
        self.runs
            .iter()
            .flat_map(|r| std::iter::repeat_n(r.signal, r.length as usize))
            .collect()
    }

    /// Whether every clock in `clocks` shows `signal`.
    pub fn all(&self, clocks: std::ops::Range<u16>, signal: Signal) -> bool {
        clocks.clone().all(|c| self.signal_at(c) == Some(signal))
    }
}

/// One frame of video: its lines, from the line on which `VSYNC` turned on.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct VideoFrame {
    /// The frame's number, counting from 1 at the first `VSYNC`.
    pub number: u64,
    pub lines: Vec<VideoLine>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Trace {
    pub config: TraceConfig,
    pub cpu_cycles: Vec<CpuCycle>,
    pub register_writes: Vec<RegisterWrite>,
    /// Completed frames. The frame in progress is not included.
    pub frames: Vec<VideoFrame>,
    /// Every audio clock's sample, from when recording began.
    pub audio: Vec<Sample>,
    /// Where each frame's samples begin: its number, and the index in
    /// `audio` of its first sample.
    pub audio_frames: Vec<(u64, usize)>,
    current_frame: VideoFrame,
    current_line: VideoLine,
}

impl Trace {
    pub fn new(config: TraceConfig) -> Trace {
        Trace {
            config,
            ..Trace::default()
        }
    }

    pub(crate) fn cpu_cycle(&mut self, cycle: CpuCycle) {
        if self.config.cpu_cycles {
            self.cpu_cycles.push(cycle);
        }
    }

    pub(crate) fn register_write(&mut self, write: RegisterWrite) {
        if self.config.register_writes {
            self.register_writes.push(write);
        }
    }

    /// The TIA output `signal` at color clock `clock`, the last of its line
    /// if `line_ended`. The clock is the TIA's own, so a recording that
    /// begins mid-line places that line's runs where they were output.
    pub(crate) fn video(&mut self, clock: u16, signal: Signal, line_ended: bool) {
        if !self.config.video {
            return;
        }
        self.current_line.push(clock, signal);
        if line_ended {
            debug_assert_eq!(clock, CLOCKS_PER_LINE - 1);
            self.current_frame.lines.push(std::mem::take(&mut self.current_line));
        }
    }

    pub(crate) fn audio(&mut self, sample: Sample) {
        if self.config.audio {
            self.audio.push(sample);
        }
    }

    /// `VSYNC` turned on, starting frame `number`; the line in progress
    /// becomes its line 0. Lines before the first frame are discarded.
    pub(crate) fn frame_started(&mut self, number: u64) {
        if self.config.audio {
            self.audio_frames.push((number, self.audio.len()));
        }
        if !self.config.video {
            return;
        }
        let done = std::mem::replace(
            &mut self.current_frame,
            VideoFrame {
                number,
                lines: Vec::new(),
            },
        );
        if done.number > 0 {
            self.frames.push(done);
        }
    }

    /// The samples of frames `first` to `end - 1`, if all were recorded.
    pub fn audio_of_frames(&self, first: u64, end: u64) -> Option<&[Sample]> {
        let start = |n: u64| self.audio_frames.iter().find(|(f, _)| *f == n).map(|(_, i)| *i);
        Some(&self.audio[start(first)?..start(end)?])
    }

    /// Writes to `chip`'s `register` in frame `frame`.
    pub fn writes_to(&self, chip: Chip, register: u8, frame: u64) -> impl Iterator<Item = &RegisterWrite> {
        self.register_writes
            .iter()
            .filter(move |w| w.chip == chip && w.register == register && w.frame == frame)
    }
}
