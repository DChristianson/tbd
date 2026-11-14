//! The TIA's two audio channels. See `TIA-AUDIO-1` to `TIA-AUDIO-4` in
//! docs/target-model.md.
//!
//! Each channel has a 4-bit control (`AUDC`), a 5-bit divider (`AUDF`), and a
//! 4-bit volume (`AUDV`). The TIA clocks audio twice a line, each clock in
//! two phases: phase 0 at color clocks 9 and 81, phase 1 at 37 and 149. On
//! phase 0 a channel's divider counts, enabling its clock every `AUDF + 1`
//! audio clocks; on an enabled clock, phase 0 and phase 1 step its 5-bit
//! noise counter and 4-bit pulse counter, whose feedback `AUDC` selects. The
//! channel's output is bit 0 of the pulse counter, times `AUDV`.
//!
//! The counters and their feedback are the circuit as Stella (`AudioChannel`),
//! Gopher2600, and 6502.ts (`PCMChannel`) all model it, after Chris Brenner's
//! analysis of the TIA's schematics; the probe ROM `probe-audio.asm` checks the
//! simulator against two of them sample for sample (E29).

/// The color clocks of a line on which each phase ticks.
pub const PHASE0_CLOCKS: [u16; 2] = [9, 81];
pub const PHASE1_CLOCKS: [u16; 2] = [37, 149];

pub const AUDC0: u8 = 0x15;
pub const AUDC1: u8 = 0x16;
pub const AUDF0: u8 = 0x17;
pub const AUDF1: u8 = 0x18;
pub const AUDV0: u8 = 0x19;
pub const AUDV1: u8 = 0x1A;

/// One channel's registers and counters.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Channel {
    pub audc: u8,
    pub audf: u8,
    pub audv: u8,
    /// Whether this audio clock steps the counters: the divider reached `AUDF`
    /// on the last phase 0.
    clock_enable: bool,
    noise_feedback: bool,
    noise_bit4: bool,
    pulse_hold: bool,
    /// The divider, counting audio clocks up to `AUDF`.
    pub divider: u8,
    /// The 4-bit pulse counter, whose bit 0 is the output.
    pub pulse: u8,
    /// The 5-bit noise counter.
    pub noise: u8,
}

impl Channel {
    /// The level the channel outputs: 0 to 15.
    pub fn output(&self) -> u8 {
        (self.pulse & 0x01) * self.audv
    }

    fn phase0(&mut self) {
        if self.clock_enable {
            self.noise_bit4 = self.noise & 0x01 != 0;
            self.pulse_hold = match self.audc & 0x03 {
                0 | 1 => false,
                2 => self.noise & 0x1E != 0x02,
                _ => !self.noise_bit4,
            };
            self.noise_feedback = if self.audc & 0x03 == 0 {
                (self.pulse ^ self.noise) & 0x01 != 0
                    || !(self.noise != 0 || self.pulse != 0x0A)
                    || self.audc & 0x0C == 0
            } else {
                ((self.noise & 0x04 != 0) ^ (self.noise & 0x01 != 0)) || self.noise == 0
            };
        }
        self.clock_enable = self.divider == self.audf;
        self.divider = if self.divider == self.audf || self.divider == 0x1F {
            0
        } else {
            self.divider + 1
        };
    }

    fn phase1(&mut self) {
        if !self.clock_enable {
            return;
        }
        let pulse_feedback = match self.audc >> 2 {
            0 => ((self.pulse & 0x02 != 0) ^ (self.pulse & 0x01 != 0)) && self.pulse != 0x0A && self.audc & 0x03 != 0,
            1 => self.pulse & 0x08 == 0,
            2 => !self.noise_bit4,
            _ => !(self.pulse & 0x02 != 0 || self.pulse & 0x0E == 0),
        };
        self.noise >>= 1;
        if self.noise_feedback {
            self.noise |= 0x10;
        }
        if !self.pulse_hold {
            self.pulse = !(self.pulse >> 1) & 0x07;
            if pulse_feedback {
                self.pulse |= 0x08;
            }
        }
    }
}

/// Both channels.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Audio {
    pub channels: [Channel; 2],
}

/// What an audio clock produced: each channel's level after its phase 1.
pub type Sample = [u8; 2];

impl Audio {
    /// A write to one of the audio registers, which a channel uses from its
    /// next phase. Returns whether `register` is one.
    pub fn write(&mut self, register: u8, value: u8) -> bool {
        let (n, field) = match register {
            AUDC0 | AUDC1 => ((register - AUDC0) as usize, 0),
            AUDF0 | AUDF1 => ((register - AUDF0) as usize, 1),
            AUDV0 | AUDV1 => ((register - AUDV0) as usize, 2),
            _ => return false,
        };
        let c = &mut self.channels[n];
        match field {
            0 => c.audc = value & 0x0F,
            1 => c.audf = value & 0x1F,
            _ => c.audv = value & 0x0F,
        }
        true
    }

    /// Color clock `clock` of a line: a phase, if one ticks there. Returns
    /// the sample an audio clock produced, after its phase 1.
    pub fn clock(&mut self, clock: u16) -> Option<Sample> {
        if PHASE0_CLOCKS.contains(&clock) {
            self.channels.iter_mut().for_each(Channel::phase0);
        } else if PHASE1_CLOCKS.contains(&clock) {
            self.channels.iter_mut().for_each(Channel::phase1);
            return Some([self.channels[0].output(), self.channels[1].output()]);
        }
        None
    }
}

/// A WAV file of `samples`, for listening: 8-bit mono, the two channels
/// summed, one sample an audio clock, at `rate` samples a second (two a
/// line: 31,440 on 262 lines at 60 frames a second). The levels are mixed
/// linearly, not as the TIA's output stage mixes them.
pub fn wav(samples: &[Sample], rate: u32) -> Vec<u8> {
    let data: Vec<u8> = samples
        .iter()
        .map(|[a, b]| ((u32::from(*a) + u32::from(*b)) * 255 / 30) as u8)
        .collect();
    let mut out = Vec::with_capacity(44 + data.len());
    let u32le = |v: u32| v.to_le_bytes();
    let u16le = |v: u16| v.to_le_bytes();
    out.extend(b"RIFF");
    out.extend(u32le(36 + data.len() as u32));
    out.extend(b"WAVEfmt ");
    out.extend(u32le(16));
    out.extend(u16le(1)); // PCM
    out.extend(u16le(1)); // mono
    out.extend(u32le(rate));
    out.extend(u32le(rate)); // bytes a second
    out.extend(u16le(1)); // bytes a sample
    out.extend(u16le(8)); // bits a sample
    out.extend(b"data");
    out.extend(u32le(data.len() as u32));
    out.extend(data);
    out
}
