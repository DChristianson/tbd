//! The TIA subset: the color clock counter, frames and lines, `WSYNC`,
//! `VSYNC`, `VBLANK`, `COLUBK`, the playfield (`PF0`-`PF2`, `CTRLPF`,
//! `COLUPF`, and in score mode `COLUP0` and `COLUP1`), players 0 and 1,
//! missile 0, and the ball (`GRP0`, `GRP1`, `ENAM0`, `ENABL`, `NUSIZ0`'s
//! missile width, `CTRLPF`'s ball width, `COLUP0`, `COLUP1`, `COLUPF`,
//! `RESP0`, `RESP1`, `RESM0`, `RESBL`, `HMP0`, `HMP1`, `HMM0`, `HMBL`,
//! `HMCLR`, `HMOVE`), the video output, the collision latches for those
//! four objects, the fire buttons' inputs, `INPT4` and `INPT5`, and the
//! audio channels (`audio.rs`).
//!
//! Writes to other registers are stored but have no effect: missile 1,
//! player copies and stretching, reflection, vertical delay, the
//! playfield's collisions, and the paddles come later. `HMOVE` is modeled as it is
//! conventionally used, strobed at the start of a line: movement ticks that
//! would fall outside horizontal blank are not applied.
//! Recording what the TIA does is the trace's job (`trace.rs`).
//! Facts implemented here are recorded in docs/target-model.md under `TIA-*`.

use crate::audio::{Audio, Sample};

/// Color clocks per line.
pub const CLOCKS_PER_LINE: u16 = 228;
/// Color clocks of horizontal blank at the start of each line.
pub const HBLANK_CLOCKS: u16 = 68;

pub const VSYNC: u8 = 0x00;
pub const VBLANK: u8 = 0x01;
pub const WSYNC: u8 = 0x02;
pub const COLUP0: u8 = 0x06;
pub const COLUP1: u8 = 0x07;
pub const COLUPF: u8 = 0x08;
pub const COLUBK: u8 = 0x09;
pub const CTRLPF: u8 = 0x0A;
pub const PF0: u8 = 0x0D;
pub const PF1: u8 = 0x0E;
pub const PF2: u8 = 0x0F;
pub const NUSIZ0: u8 = 0x04;
pub const RESP0: u8 = 0x10;
pub const RESP1: u8 = 0x11;
pub const RESM0: u8 = 0x12;
pub const RESBL: u8 = 0x14;
pub const GRP0: u8 = 0x1B;
pub const GRP1: u8 = 0x1C;
pub const ENAM0: u8 = 0x1D;
pub const ENABL: u8 = 0x1F;
pub const HMP0: u8 = 0x20;
pub const HMP1: u8 = 0x21;
pub const HMM0: u8 = 0x22;
pub const HMBL: u8 = 0x24;
pub const HMOVE: u8 = 0x2A;
pub const HMCLR: u8 = 0x2B;
pub const CXCLR: u8 = 0x2C;

/// Read registers, by address modulo 16. See `TIA-COLLIDE-1` and
/// `TIA-INPUT-1`.
pub const CXM0P: u8 = 0x00;
pub const CXP0FB: u8 = 0x02;
pub const CXP1FB: u8 = 0x03;
pub const CXM0FB: u8 = 0x04;
pub const CXPPMM: u8 = 0x07;
pub const INPT4: u8 = 0x0C;
pub const INPT5: u8 = 0x0D;

/// Color clocks from a write to `PF0`, `PF1`, or `PF2` until the playfield
/// uses the new value. See `TIA-PF-3`.
pub const PF_WRITE_DELAY: u8 = 2;
/// The visible clock at which the TIA reads `CTRLPF`'s reflect bit for the
/// right half of the line. See `TIA-PF-1`.
pub const REFLECT_CLOCK: u16 = 79;
/// Color clocks from a write to `GRP0`, `GRP1`, `ENAM0`, or `ENABL` until
/// the object uses the new value. See `TIA-OBJ-5`.
pub const OBJECT_WRITE_DELAY: u8 = 1;
/// Color clocks from a write to `HMP0`, `HMP1`, `HMM0`, `HMBL`, or `HMCLR`
/// until it takes effect, and from an `HMOVE` strobe until motion starts. See `TIA-OBJ-4`.
pub const HM_WRITE_DELAY: u8 = 2;
pub const HMOVE_DELAY: u8 = 6;
/// Color clocks of the visible line that `HMOVE` blanks. See `TIA-OBJ-4`.
pub const HMOVE_BLANK_CLOCKS: u16 = 8;

/// The value of an object's position counter at which it starts drawing,
/// and the values a strobe sets it to. See `TIA-OBJ-3`.
pub const START_DECODE: u8 = 156;
pub const RESET_VISIBLE: u8 = 157;
pub const RESET_HBLANK: u8 = 159;
pub const RESET_LATE_HBLANK: u8 = 158;

/// The kinds of object modeled, which differ in when they start drawing,
/// and in what a strobe does to one that is drawing. See `TIA-OBJ-3` and
/// `TIA-OBJ-7`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Player,
    Missile,
    Ball,
}

impl Kind {
    /// Ticks from the start decode to the first pixel.
    fn start_delay(self) -> i8 {
        match self {
            Kind::Player => 5,
            Kind::Missile | Kind::Ball => 4,
        }
    }
}

/// A movable object's position and drawing state. See `TIA-OBJ-3` and
/// `TIA-OBJ-4`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Object {
    pub kind: Kind,
    /// The position counter, 0-159, ticked once per visible color clock
    /// and once per `HMOVE` motion tick.
    pub counter: u8,
    /// While drawing: ticks since the first pixel, negative during the
    /// start delay. `None` when not drawing.
    pub render: Option<i8>,
    /// Motion ticks to apply in the current `HMOVE`: `(HMxx >> 4) ^ 8`.
    pub motion: u8,
    /// Whether the current `HMOVE` is still moving this object.
    pub moving: bool,
}

impl Object {
    fn new(kind: Kind, hm: u8) -> Object {
        Object {
            kind,
            counter: 0,
            render: None,
            motion: (hm >> 4) ^ 8,
            moving: false,
        }
    }

    /// Advance one tick: start drawing at the start decode, else move the
    /// drawing on, ending it after `width` pixels.
    fn tick(&mut self, width: i8) {
        if self.counter == START_DECODE {
            self.render = Some(-self.kind.start_delay());
        } else if let Some(r) = self.render {
            self.render = (r + 1 < width).then_some(r + 1);
        }
        self.counter = if self.counter == 159 { 0 } else { self.counter + 1 };
    }

    /// A `RESPx`, `RESMx`, or `RESBL` strobe, setting the counter to
    /// `value`. A player or missile strobed during its start delay starts
    /// again; the ball always starts drawing, on the strobe line itself.
    fn reset(&mut self, value: u8) {
        self.counter = value;
        let restart_below = match self.kind {
            Kind::Player => -1,
            Kind::Missile => 0,
            Kind::Ball => {
                self.render = Some(-self.kind.start_delay() + (value - RESET_VISIBLE) as i8);
                return;
            }
        };
        if let Some(r) = self.render {
            if r < restart_below {
                self.render = Some(-self.kind.start_delay() + (value - RESET_VISIBLE) as i8);
            }
        }
    }

    /// The pixel being drawn at this clock, 0 to width - 1, if any.
    fn pixel(&self) -> Option<u8> {
        self.render.filter(|&r| r >= 0).map(|r| r as u8)
    }
}

/// The video output during one color clock. See `TIA-OUTPUT-1`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Signal {
    /// Vertical sync (`VSYNC` on).
    Sync,
    /// Horizontal blank (the first 68 clocks of every line) or vertical
    /// blank (`VBLANK` on).
    Blank,
    /// A visible color: the value of the color register, bit 0 clear.
    Color(u8),
}

/// The TIA's state: its registers and where the beam is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tia {
    /// The color clock within the current line, 0-227.
    pub clock: u16,
    /// The current line within the current frame.
    pub line: u32,
    /// Frames begun since power-on: 0 until the first `VSYNC`.
    pub frame: u64,
    /// The last value written to each register, by address modulo 64.
    pub registers: [u8; 64],
    /// Set by a `WSYNC` strobe; the CPU halts at its next read until the
    /// next line starts. See `TIA-WSYNC-1`.
    pub wsync: bool,
    /// The length in lines of each completed frame.
    pub frame_lengths: Vec<u32>,
    /// The values of `PF0`, `PF1`, and `PF2` the playfield is drawn from,
    /// which follow the registers `PF_WRITE_DELAY` clocks behind.
    pub playfield: [u8; 3],
    /// Writes to `PF0`-`PF2` not yet in `playfield`: (clocks left,
    /// register, value).
    pub playfield_pending: Vec<(u8, u8, u8)>,
    /// Whether the current playfield pixel is on, read at its first clock.
    pub playfield_pixel: bool,
    /// Whether the right half of this line is reflected, read at
    /// `REFLECT_CLOCK`.
    pub reflect: bool,
    /// Writes not yet in effect, other than the playfield's: (clocks left,
    /// register, value). See `TIA-OBJ-4` and `TIA-OBJ-5`.
    pub pending: Vec<(u8, u8, u8)>,
    /// The values of `GRP0`, `GRP1`, `ENAM0`, and `ENABL` the objects are
    /// drawn from, which follow the registers `OBJECT_WRITE_DELAY` clocks
    /// behind.
    pub grp0: u8,
    pub grp1: u8,
    pub enam0: u8,
    pub enabl: u8,
    pub player0: Object,
    pub player1: Object,
    pub missile0: Object,
    pub ball: Object,
    /// Whether `HMOVE` has extended this line's horizontal blank by
    /// `HMOVE_BLANK_CLOCKS`.
    pub extended_hblank: bool,
    /// Motion ticks counted since the current `HMOVE` started, while any
    /// object is still moving.
    pub motion_clock: Option<u8>,
    /// The collision latches, as the eight read registers' bits 7 and 6.
    /// See `TIA-COLLIDE-1`.
    pub collisions: [u8; 8],
    /// Whether each fire button, joystick 0's and joystick 1's, is down.
    /// Set with `set_fire`. See `TIA-INPUT-1`.
    pub fire: [bool; 2],
    /// Whether each fire button has been down since `VBLANK`'s latch bit
    /// was turned on.
    fire_latched: [bool; 2],
    /// The audio channels. See `TIA-AUDIO-1`.
    pub audio: Audio,
    /// The sample the last color clock's audio clock produced, if it ended
    /// one, for the trace.
    pub sample: Option<Sample>,
}

/// What a color clock or a write did, for the trace.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    None,
    /// The clock ended a line.
    LineEnded,
    /// The write turned `VSYNC` on, starting a frame.
    FrameStarted,
}

impl Tia {
    pub fn new(registers: [u8; 64]) -> Tia {
        Tia {
            clock: 0,
            line: 0,
            frame: 0,
            registers,
            wsync: false,
            frame_lengths: Vec::new(),
            playfield: [
                registers[PF0 as usize],
                registers[PF1 as usize],
                registers[PF2 as usize],
            ],
            playfield_pending: Vec::new(),
            playfield_pixel: false,
            reflect: registers[CTRLPF as usize] & 0x01 != 0,
            pending: Vec::new(),
            grp0: registers[GRP0 as usize],
            grp1: registers[GRP1 as usize],
            enam0: registers[ENAM0 as usize],
            enabl: registers[ENABL as usize],
            player0: Object::new(Kind::Player, registers[HMP0 as usize]),
            player1: Object::new(Kind::Player, registers[HMP1 as usize]),
            missile0: Object::new(Kind::Missile, registers[HMM0 as usize]),
            ball: Object::new(Kind::Ball, registers[HMBL as usize]),
            extended_hblank: false,
            motion_clock: None,
            collisions: [0; 8],
            fire: [false; 2],
            fire_latched: [false; 2],
            audio: {
                let mut audio = Audio::default();
                for r in crate::audio::AUDC0..=crate::audio::AUDV1 {
                    audio.write(r, registers[r as usize]);
                }
                audio
            },
            sample: None,
        }
    }

    /// Press or release fire button `n`, 0 or 1. With `VBLANK`'s latch on,
    /// a press is kept until the latch is turned off. See `TIA-INPUT-1`.
    pub fn set_fire(&mut self, n: usize, down: bool) {
        self.fire[n] = down;
        if down && self.latch_inputs() {
            self.fire_latched[n] = true;
        }
    }

    /// Whether `VBLANK`'s bit 6 latches the fire buttons.
    fn latch_inputs(&self) -> bool {
        self.registers[VBLANK as usize] & 0x40 != 0
    }

    /// The bits a read of `register` drives: 7 and 6, the rest 0. The
    /// board fills the undriven bits (`BOARD-TIA-READ-1`). Reads decode
    /// four address lines.
    pub fn read(&self, register: u8) -> u8 {
        match register & 0x0F {
            r @ 0..=7 => self.collisions[r as usize],
            r @ (INPT4 | INPT5) => {
                let n = (r - INPT4) as usize;
                if self.fire[n] || self.fire_latched[n] {
                    0x00
                } else {
                    0x80
                }
            }
            // The paddles' inputs, INPT0-INPT3, are not modeled (milestone 10).
            _ => 0x00,
        }
    }

    /// Whether the TIA is in horizontal blank, extended by `HMOVE`.
    fn hblank(&self) -> bool {
        self.clock < HBLANK_CLOCKS || (self.extended_hblank && self.clock < HBLANK_CLOCKS + HMOVE_BLANK_CLOCKS)
    }

    /// The missile's width in clocks, from `NUSIZ0`. See `TIA-OBJ-2`.
    fn missile_width(&self) -> i8 {
        1 << ((self.registers[NUSIZ0 as usize] >> 4) & 0x03)
    }

    /// The ball's width in clocks, from `CTRLPF`. See `TIA-OBJ-7`.
    fn ball_width(&self) -> i8 {
        1 << ((self.registers[CTRLPF as usize] >> 4) & 0x03)
    }

    /// Whether player 0 or missile 0 draws a pixel at this clock. See
    /// `TIA-OBJ-1` and `TIA-OBJ-2`.
    fn object0_pixel(&self) -> bool {
        let player = self.player0.pixel().is_some_and(|n| self.grp0 & (0x80 >> n) != 0);
        let missile = self.missile0.pixel().is_some() && self.enam0 & 0x02 != 0;
        player || missile
    }

    /// Whether player 1 draws a pixel at this clock. See `TIA-OBJ-1`.
    fn player1_pixel(&self) -> bool {
        self.player1.pixel().is_some_and(|n| self.grp1 & (0x80 >> n) != 0)
    }

    /// Whether the ball draws a pixel at this clock. See `TIA-OBJ-7`.
    fn ball_pixel(&self) -> bool {
        self.ball.pixel().is_some() && self.enabl & 0x02 != 0
    }

    pub fn vsync(&self) -> bool {
        self.registers[VSYNC as usize] & 0x02 != 0
    }

    pub fn vblank(&self) -> bool {
        self.registers[VBLANK as usize] & 0x02 != 0
    }

    /// The output during the current color clock. See `TIA-OUTPUT-1`,
    /// `TIA-PF-2`, and `TIA-OBJ-6`.
    pub fn signal(&self) -> Signal {
        if self.vsync() {
            return Signal::Sync;
        } else if self.hblank() || self.vblank() {
            return Signal::Blank;
        }
        let ctrlpf = self.registers[CTRLPF as usize];
        let playfield_priority = ctrlpf & 0x04 != 0;
        // Score mode, unless playfield priority is also set.
        let score = ctrlpf & 0x06 == 0x02;
        let playfield = self
            .playfield_pixel
            .then(|| match (score, self.clock - HBLANK_CLOCKS < 80) {
                (true, true) => COLUP0,
                (true, false) => COLUP1,
                (false, _) => COLUPF,
            });
        // Most clocks draw no object: then only the playfield can show.
        let drawing = [&self.player0, &self.player1, &self.missile0, &self.ball]
            .iter()
            .any(|o| o.render.is_some());
        if !drawing {
            return Signal::Color(self.registers[playfield.unwrap_or(COLUBK) as usize] & 0xFE);
        }
        let object0 = self.object0_pixel().then_some(COLUP0);
        let player1 = self.player1_pixel().then_some(COLUP1);
        let ball = self.ball_pixel().then_some(COLUPF);
        let register = if playfield_priority {
            playfield.or(ball).or(object0).or(player1)
        } else if score {
            object0.or(playfield).or(player1).or(ball)
        } else {
            object0.or(player1).or(playfield).or(ball)
        };
        Signal::Color(self.registers[register.unwrap_or(COLUBK) as usize] & 0xFE)
    }

    /// Whether playfield pixel `n` (0-39, left to right) is on, from the
    /// playfield's current values. See `TIA-PF-1`.
    fn playfield_bit(&self, n: u16) -> bool {
        let half = if n < 20 {
            n
        } else if self.reflect {
            39 - n
        } else {
            n - 20
        };
        let [pf0, pf1, pf2] = self.playfield;
        match half {
            0..=3 => pf0 >> (4 + half) & 1 != 0,
            4..=11 => pf1 >> (11 - half) & 1 != 0,
            _ => pf2 >> (half - 12) & 1 != 0,
        }
    }

    /// Apply the playfield writes that are due, and read the playfield at
    /// the start of each pixel. Called before each clock's output.
    fn update_playfield(&mut self) {
        let playfield = &mut self.playfield;
        self.playfield_pending.retain_mut(|(left, register, value)| {
            if *left > 0 {
                return true;
            }
            playfield[(*register - PF0) as usize] = *value;
            false
        });
        if self.clock >= HBLANK_CLOCKS {
            let x = self.clock - HBLANK_CLOCKS;
            if x == REFLECT_CLOCK {
                self.reflect = self.registers[CTRLPF as usize] & 0x01 != 0;
            }
            if x % 4 == 0 {
                self.playfield_pixel = self.playfield_bit(x / 4);
            }
        }
    }

    /// Apply the object writes that are due. Called before each clock's
    /// output.
    fn update_objects(&mut self) {
        if self.pending.iter().all(|&(left, _, _)| left > 0) {
            return;
        }
        let mut due = Vec::new();
        self.pending.retain(|&(left, register, value)| {
            if left > 0 {
                return true;
            }
            due.push((register, value));
            false
        });
        for (register, value) in due {
            match register {
                GRP0 => self.grp0 = value,
                GRP1 => self.grp1 = value,
                ENAM0 => self.enam0 = value,
                ENABL => self.enabl = value,
                HMP0 => self.player0.motion = (value >> 4) ^ 8,
                HMP1 => self.player1.motion = (value >> 4) ^ 8,
                HMM0 => self.missile0.motion = (value >> 4) ^ 8,
                HMBL => self.ball.motion = (value >> 4) ^ 8,
                HMCLR => {
                    for object in self.objects_mut() {
                        object.motion = 8;
                    }
                }
                HMOVE => {
                    self.motion_clock = Some(0);
                    self.extended_hblank = true;
                    for object in self.objects_mut() {
                        object.moving = true;
                    }
                }
                _ => {}
            }
        }
    }

    /// Latch the collisions of the objects drawing at this clock. Called
    /// only outside horizontal and vertical blank: the three emulators latch
    /// nothing with `VBLANK` on. See `TIA-COLLIDE-1` and `TIA-COLLIDE-2`.
    fn collide(&mut self) {
        let p0 = self.player0.pixel().is_some_and(|n| self.grp0 & (0x80 >> n) != 0);
        let p1 = self.player1_pixel();
        let m0 = self.missile0.pixel().is_some() && self.enam0 & 0x02 != 0;
        let bl = self.ball_pixel();
        let c = &mut self.collisions;
        if m0 && p1 {
            c[CXM0P as usize] |= 0x80;
        }
        if m0 && p0 {
            c[CXM0P as usize] |= 0x40;
        }
        if p0 && bl {
            c[CXP0FB as usize] |= 0x40;
        }
        if p1 && bl {
            c[CXP1FB as usize] |= 0x40;
        }
        if m0 && bl {
            c[CXM0FB as usize] |= 0x40;
        }
        if p0 && p1 {
            c[CXPPMM as usize] |= 0x80;
        }
    }

    /// The objects, in a fixed order: player 0, player 1, missile 0, and
    /// the ball.
    fn objects_mut(&mut self) -> [&mut Object; 4] {
        [&mut self.player0, &mut self.player1, &mut self.missile0, &mut self.ball]
    }

    /// Each object's width in clocks, in the order of `objects_mut`.
    fn widths(&self) -> [i8; 4] {
        [8, 8, self.missile_width(), self.ball_width()]
    }

    /// Tick the objects: once per visible clock, and, during an `HMOVE`,
    /// once per motion tick in horizontal blank. See `TIA-OBJ-3` and
    /// `TIA-OBJ-4`.
    fn tick_objects(&mut self) {
        let hblank = self.hblank();
        let widths = self.widths();
        if !hblank {
            for (object, w) in self.objects_mut().into_iter().zip(widths) {
                object.tick(w);
            }
        }
        let Some(motion_clock) = self.motion_clock else { return };
        if self.clock % 4 != 0 {
            return;
        }
        // The motion counter has 4 bits: past its 15th tick it reads 0, so a
        // motion of 0 written after its tick still stops there, and any
        // other that missed its tick keeps the object moving (TIA-OBJ-8).
        let counter = if motion_clock > 15 { 0 } else { motion_clock };
        let mut moving = false;
        for (object, w) in self.objects_mut().into_iter().zip(widths) {
            if !object.moving {
                continue;
            }
            if counter == object.motion {
                object.moving = false;
            } else if hblank {
                object.tick(w);
            }
            moving |= object.moving;
        }
        self.motion_clock = moving.then_some(motion_clock.saturating_add(1));
    }

    /// Output one color clock and advance. Returns the signal output.
    pub fn clock(&mut self) -> (Signal, Event) {
        self.update_playfield();
        self.update_objects();
        let signal = self.signal();
        self.sample = self.audio.clock(self.clock);
        if !self.hblank()
            && !self.vblank()
            && [&self.player0, &self.player1, &self.missile0, &self.ball]
                .iter()
                .any(|o| o.render.is_some())
        {
            self.collide();
        }
        self.tick_objects();
        if !self.playfield_pending.is_empty() || !self.pending.is_empty() {
            for (left, _, _) in self.playfield_pending.iter_mut().chain(self.pending.iter_mut()) {
                *left = left.saturating_sub(1);
            }
        }
        self.clock += 1;
        if self.clock == CLOCKS_PER_LINE {
            self.clock = 0;
            self.line += 1;
            self.extended_hblank = false;
            return (signal, Event::LineEnded);
        }
        (signal, Event::None)
    }

    /// The counter value a `RESPx`, `RESM0`, or `RESBL` strobe sets now. See
    /// `TIA-OBJ-3`.
    fn reset_value(&self) -> u8 {
        match (self.hblank(), self.clock >= HBLANK_CLOCKS + 5) {
            (true, true) => RESET_LATE_HBLANK,
            (true, false) => RESET_HBLANK,
            (false, _) => RESET_VISIBLE,
        }
    }

    /// A register write, applied at the current color clock.
    pub fn write(&mut self, register: u8, value: u8) -> Event {
        let was_vsync = self.vsync();
        self.registers[register as usize] = value;
        match register {
            WSYNC => self.wsync = true,
            crate::audio::AUDC0..=crate::audio::AUDV1 => {
                self.audio.write(register, value);
            }
            CXCLR => self.collisions = [0; 8],
            // The latch off releases it; on, it holds a button already down.
            VBLANK if !self.latch_inputs() => self.fire_latched = [false; 2],
            VBLANK => {
                for n in 0..2 {
                    self.fire_latched[n] |= self.fire[n];
                }
            }
            PF0 | PF1 | PF2 => self.playfield_pending.push((PF_WRITE_DELAY, register, value)),
            GRP0 | GRP1 | ENAM0 | ENABL => self.pending.push((OBJECT_WRITE_DELAY, register, value)),
            HMP0 | HMP1 | HMM0 | HMBL | HMCLR => self.pending.push((HM_WRITE_DELAY, register, value)),
            HMOVE => self.pending.push((HMOVE_DELAY, register, value)),
            RESP0 | RESP1 | RESM0 | RESBL => {
                let v = self.reset_value();
                match register {
                    RESP0 => self.player0.reset(v),
                    RESP1 => self.player1.reset(v),
                    RESM0 => self.missile0.reset(v),
                    _ => self.ball.reset(v),
                }
            }
            VSYNC if self.vsync() && !was_vsync => {
                self.start_frame();
                return Event::FrameStarted;
            }
            _ => {}
        }
        Event::None
    }

    /// `VSYNC` has turned on: the current line is line 0 of a new frame.
    /// See `TIA-FRAME-1`.
    fn start_frame(&mut self) {
        if self.frame > 0 {
            self.frame_lengths.push(self.line);
        }
        self.frame += 1;
        self.line = 0;
    }
}
