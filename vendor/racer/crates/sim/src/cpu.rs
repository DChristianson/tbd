//! The 6507 CPU: every documented NMOS 6502 instruction, cycle by cycle,
//! plus the undocumented `LXA #imm` ($AB), which the standard `CLEAN_START`
//! macro uses (tbd: undocumented opcodes are allowed in model-written
//! programs; this was added in tbd's vendored copy, see vendor/racer/NOTICE.md).
//!
//! Each instruction is executed as the exact sequence of bus cycles the
//! hardware performs, including the reads whose values are discarded ("dummy
//! reads"). Every call to [`Bus::read`] or [`Bus::write`] is one cycle, so the
//! bus sees the same activity, in the same order, as real hardware. Facts
//! implemented here are recorded in docs/target-model.md under `CPU-*`.
//!
//! Semantics are generic over the value domain (decision 0001). Addresses
//! and the program counter are concrete; a value used as an address must be
//! concrete, or execution stops with [`Stop::SymbolicAddress`].
//!
//! The 6507 is a 6502 with 13 address lines and no interrupt inputs. The CPU
//! here produces full 16-bit addresses; the board decides which lines exist.

use crate::domain::Domain;

pub const FLAG_C: u8 = 0x01;
pub const FLAG_Z: u8 = 0x02;
pub const FLAG_I: u8 = 0x04;
pub const FLAG_D: u8 = 0x08;
/// Not a stored flag: set in the copy of P pushed by `PHP` and `BRK`.
pub const FLAG_B: u8 = 0x10;
/// Not a stored flag: always reads as 1.
pub const FLAG_U: u8 = 0x20;
pub const FLAG_V: u8 = 0x40;
pub const FLAG_N: u8 = 0x80;

/// CPU registers. The program counter is always concrete.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Regs<T> {
    pub a: T,
    pub x: T,
    pub y: T,
    pub s: T,
    pub p: T,
    pub pc: u16,
}

impl<T> Regs<T> {
    pub fn map<U>(&self, mut f: impl FnMut(&T) -> U) -> Regs<U> {
        Regs {
            a: f(&self.a),
            x: f(&self.x),
            y: f(&self.y),
            s: f(&self.s),
            p: f(&self.p),
            pc: self.pc,
        }
    }
}

/// Why an instruction could not be executed. After a stop, the registers and
/// bus may reflect a partly executed instruction.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Stop {
    /// An opcode that is neither a documented NMOS 6502 instruction nor one of
    /// the undocumented ones implemented here (`LXA`).
    UnknownOpcode { pc: u16, opcode: u8 },
    /// The opcode byte is not concrete.
    SymbolicOpcode { pc: u16 },
    /// A value used as an address (operand, index, pointer, or stack pointer)
    /// is not concrete.
    SymbolicAddress { pc: u16 },
    /// A branch depends on a flag that is not concrete.
    SymbolicBranch { pc: u16 },
    /// Decimal-mode arithmetic, or a decimal flag that is not concrete, with
    /// operands that are not concrete. Decimal mode is executed concretely only.
    SymbolicDecimal { pc: u16 },
}

/// The CPU's view of the rest of the machine. Each call is one bus cycle.
pub trait Bus<D: Domain> {
    fn read(&mut self, d: &mut D, addr: u16) -> D::Byte;
    fn write(&mut self, d: &mut D, addr: u16, value: D::Byte);
}

/// Instructions, by mnemonic.
#[rustfmt::skip]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Op {
    Adc, And, Asl, Bcc, Bcs, Beq, Bit, Bmi, Bne, Bpl, Brk, Bvc, Bvs, Clc,
    Cld, Cli, Clv, Cmp, Cpx, Cpy, Dec, Dex, Dey, Eor, Inc, Inx, Iny, Jmp,
    Jsr, Lda, Ldx, Ldy, Lsr, Nop, Ora, Pha, Php, Pla, Plp, Rol, Ror, Rti,
    Rts, Sbc, Sec, Sed, Sei, Sta, Stx, Sty, Tax, Tay, Tsx, Txa, Txs, Tya,
    /// Undocumented: `A = X = (A | $EE) & imm`, as Stella implements it.
    Lxa,
}

/// Addressing modes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Mode {
    /// No operand.
    Implied,
    /// Operates on A.
    Accumulator,
    Immediate,
    ZeroPage,
    ZeroPageX,
    ZeroPageY,
    Absolute,
    AbsoluteX,
    AbsoluteY,
    /// `(zp,X)`
    IndirectX,
    /// `(zp),Y`
    IndirectY,
    /// `(abs)`, used only by `JMP`.
    Indirect,
    /// Branch offset.
    Relative,
}

/// Decode an opcode. `None` for the undocumented opcodes, except `LXA` ($AB).
#[rustfmt::skip]
pub fn decode(opcode: u8) -> Option<(Op, Mode)> {
    use Mode::*;
    use Op::*;
    Some(match opcode {
        0x69 => (Adc, Immediate), 0x65 => (Adc, ZeroPage), 0x75 => (Adc, ZeroPageX), 0x6D => (Adc, Absolute),
        0x7D => (Adc, AbsoluteX), 0x79 => (Adc, AbsoluteY), 0x61 => (Adc, IndirectX), 0x71 => (Adc, IndirectY),
        0x29 => (And, Immediate), 0x25 => (And, ZeroPage), 0x35 => (And, ZeroPageX), 0x2D => (And, Absolute),
        0x3D => (And, AbsoluteX), 0x39 => (And, AbsoluteY), 0x21 => (And, IndirectX), 0x31 => (And, IndirectY),
        0x0A => (Asl, Accumulator), 0x06 => (Asl, ZeroPage), 0x16 => (Asl, ZeroPageX), 0x0E => (Asl, Absolute),
        0x1E => (Asl, AbsoluteX),
        0x90 => (Bcc, Relative), 0xB0 => (Bcs, Relative), 0xF0 => (Beq, Relative), 0x30 => (Bmi, Relative),
        0xD0 => (Bne, Relative), 0x10 => (Bpl, Relative), 0x50 => (Bvc, Relative), 0x70 => (Bvs, Relative),
        0x24 => (Bit, ZeroPage), 0x2C => (Bit, Absolute),
        0x00 => (Brk, Implied),
        0x18 => (Clc, Implied), 0xD8 => (Cld, Implied), 0x58 => (Cli, Implied), 0xB8 => (Clv, Implied),
        0xC9 => (Cmp, Immediate), 0xC5 => (Cmp, ZeroPage), 0xD5 => (Cmp, ZeroPageX), 0xCD => (Cmp, Absolute),
        0xDD => (Cmp, AbsoluteX), 0xD9 => (Cmp, AbsoluteY), 0xC1 => (Cmp, IndirectX), 0xD1 => (Cmp, IndirectY),
        0xE0 => (Cpx, Immediate), 0xE4 => (Cpx, ZeroPage), 0xEC => (Cpx, Absolute),
        0xC0 => (Cpy, Immediate), 0xC4 => (Cpy, ZeroPage), 0xCC => (Cpy, Absolute),
        0xC6 => (Dec, ZeroPage), 0xD6 => (Dec, ZeroPageX), 0xCE => (Dec, Absolute), 0xDE => (Dec, AbsoluteX),
        0xCA => (Dex, Implied), 0x88 => (Dey, Implied),
        0x49 => (Eor, Immediate), 0x45 => (Eor, ZeroPage), 0x55 => (Eor, ZeroPageX), 0x4D => (Eor, Absolute),
        0x5D => (Eor, AbsoluteX), 0x59 => (Eor, AbsoluteY), 0x41 => (Eor, IndirectX), 0x51 => (Eor, IndirectY),
        0xE6 => (Inc, ZeroPage), 0xF6 => (Inc, ZeroPageX), 0xEE => (Inc, Absolute), 0xFE => (Inc, AbsoluteX),
        0xE8 => (Inx, Implied), 0xC8 => (Iny, Implied),
        0x4C => (Jmp, Absolute), 0x6C => (Jmp, Indirect), 0x20 => (Jsr, Absolute),
        0xA9 => (Lda, Immediate), 0xA5 => (Lda, ZeroPage), 0xB5 => (Lda, ZeroPageX), 0xAD => (Lda, Absolute),
        0xBD => (Lda, AbsoluteX), 0xB9 => (Lda, AbsoluteY), 0xA1 => (Lda, IndirectX), 0xB1 => (Lda, IndirectY),
        0xA2 => (Ldx, Immediate), 0xA6 => (Ldx, ZeroPage), 0xB6 => (Ldx, ZeroPageY), 0xAE => (Ldx, Absolute),
        0xBE => (Ldx, AbsoluteY),
        0xA0 => (Ldy, Immediate), 0xA4 => (Ldy, ZeroPage), 0xB4 => (Ldy, ZeroPageX), 0xAC => (Ldy, Absolute),
        0xBC => (Ldy, AbsoluteX),
        0x4A => (Lsr, Accumulator), 0x46 => (Lsr, ZeroPage), 0x56 => (Lsr, ZeroPageX), 0x4E => (Lsr, Absolute),
        0x5E => (Lsr, AbsoluteX),
        0xEA => (Nop, Implied),
        0x09 => (Ora, Immediate), 0x05 => (Ora, ZeroPage), 0x15 => (Ora, ZeroPageX), 0x0D => (Ora, Absolute),
        0x1D => (Ora, AbsoluteX), 0x19 => (Ora, AbsoluteY), 0x01 => (Ora, IndirectX), 0x11 => (Ora, IndirectY),
        0x48 => (Pha, Implied), 0x08 => (Php, Implied), 0x68 => (Pla, Implied), 0x28 => (Plp, Implied),
        0x2A => (Rol, Accumulator), 0x26 => (Rol, ZeroPage), 0x36 => (Rol, ZeroPageX), 0x2E => (Rol, Absolute),
        0x3E => (Rol, AbsoluteX),
        0x6A => (Ror, Accumulator), 0x66 => (Ror, ZeroPage), 0x76 => (Ror, ZeroPageX), 0x6E => (Ror, Absolute),
        0x7E => (Ror, AbsoluteX),
        0x40 => (Rti, Implied), 0x60 => (Rts, Implied),
        0xE9 => (Sbc, Immediate), 0xE5 => (Sbc, ZeroPage), 0xF5 => (Sbc, ZeroPageX), 0xED => (Sbc, Absolute),
        0xFD => (Sbc, AbsoluteX), 0xF9 => (Sbc, AbsoluteY), 0xE1 => (Sbc, IndirectX), 0xF1 => (Sbc, IndirectY),
        0x38 => (Sec, Implied), 0xF8 => (Sed, Implied), 0x78 => (Sei, Implied),
        0x85 => (Sta, ZeroPage), 0x95 => (Sta, ZeroPageX), 0x8D => (Sta, Absolute), 0x9D => (Sta, AbsoluteX),
        0x99 => (Sta, AbsoluteY), 0x81 => (Sta, IndirectX), 0x91 => (Sta, IndirectY),
        0x86 => (Stx, ZeroPage), 0x96 => (Stx, ZeroPageY), 0x8E => (Stx, Absolute),
        0x84 => (Sty, ZeroPage), 0x94 => (Sty, ZeroPageX), 0x8C => (Sty, Absolute),
        0xAA => (Tax, Implied), 0xA8 => (Tay, Implied), 0xBA => (Tsx, Implied), 0x8A => (Txa, Implied),
        0x9A => (Txs, Implied), 0x98 => (Tya, Implied),
        0xAB => (Lxa, Immediate),
        _ => return None,
    })
}

/// How an instruction uses its effective address, which decides the
/// dummy reads of the indexed modes.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Access {
    Read,
    Write,
    ReadModifyWrite,
}

/// Execute one instruction.
pub fn step<D: Domain, B: Bus<D>>(d: &mut D, r: &mut Regs<D::Byte>, bus: &mut B) -> Result<(), Stop> {
    let pc0 = r.pc;
    let opcode = bus.read(d, pc0);
    let opcode = d.concrete(&opcode).ok_or(Stop::SymbolicOpcode { pc: pc0 })?;
    r.pc = pc0.wrapping_add(1);
    let (op, mode) = decode(opcode).ok_or(Stop::UnknownOpcode { pc: pc0, opcode })?;
    Exec { d, r, bus, pc0 }.run(op, mode)
}

/// The reset sequence: seven cycles, ending with the program counter loaded
/// from the reset vector at $FFFC. The stack pointer is decremented three
/// times without writing (the reads stand in for the pushes an interrupt
/// would make), and I is set. See `CPU-RESET-1`.
pub fn reset<D: Domain, B: Bus<D>>(d: &mut D, r: &mut Regs<D::Byte>, bus: &mut B) -> Result<(), Stop> {
    let mut e = Exec { d, r, bus, pc0: 0xFFFC };
    let pc = e.r.pc;
    e.read(pc);
    e.read(pc);
    for _ in 0..3 {
        let s = e.c8(&e.r.s.clone())?;
        e.read(0x0100 | s as u16);
        e.r.s = e.d.byte(s.wrapping_sub(1));
    }
    let t = e.d.bit(true);
    e.set_flag(FLAG_I, &t);
    let lo = e.read(0xFFFC);
    let hi = e.read(0xFFFD);
    e.r.pc = e.word(&lo, &hi)?;
    Ok(())
}

struct Exec<'a, D: Domain, B: Bus<D>> {
    d: &'a mut D,
    r: &'a mut Regs<D::Byte>,
    bus: &'a mut B,
    /// Address of the instruction's opcode, for stop reasons.
    pc0: u16,
}

impl<D: Domain, B: Bus<D>> Exec<'_, D, B> {
    // --- Bus cycles ---------------------------------------------------------

    fn read(&mut self, addr: u16) -> D::Byte {
        self.bus.read(self.d, addr)
    }

    fn write(&mut self, addr: u16, value: D::Byte) {
        self.bus.write(self.d, addr, value)
    }

    /// Read the byte at PC and advance PC.
    fn fetch(&mut self) -> D::Byte {
        let v = self.read(self.r.pc);
        self.r.pc = self.r.pc.wrapping_add(1);
        v
    }

    /// The cycle in which an implied instruction reads the byte after its
    /// opcode and discards it, without advancing PC.
    fn dummy_read_pc(&mut self) {
        let pc = self.r.pc;
        self.read(pc);
    }

    fn c8(&self, b: &D::Byte) -> Result<u8, Stop> {
        self.d.concrete(b).ok_or(Stop::SymbolicAddress { pc: self.pc0 })
    }

    fn fetch8(&mut self) -> Result<u8, Stop> {
        let b = self.fetch();
        self.c8(&b)
    }

    fn word(&self, lo: &D::Byte, hi: &D::Byte) -> Result<u16, Stop> {
        Ok(u16::from_le_bytes([self.c8(lo)?, self.c8(hi)?]))
    }

    // --- Stack --------------------------------------------------------------

    fn stack_addr(&self) -> Result<u16, Stop> {
        Ok(0x0100 | self.c8(&self.r.s)? as u16)
    }

    fn push(&mut self, v: D::Byte) -> Result<(), Stop> {
        let addr = self.stack_addr()?;
        self.write(addr, v);
        let s = (addr as u8).wrapping_sub(1);
        self.r.s = self.d.byte(s);
        Ok(())
    }

    fn inc_s(&mut self) -> Result<(), Stop> {
        let s = self.c8(&self.r.s)?.wrapping_add(1);
        self.r.s = self.d.byte(s);
        Ok(())
    }

    // --- Flags --------------------------------------------------------------

    /// Set or clear the flags in `mask` according to `cond`.
    fn set_flag(&mut self, mask: u8, cond: &D::Bit) {
        let keep = self.d.byte(!mask);
        let cleared = self.d.and(&self.r.p, &keep);
        let m = self.d.byte(mask);
        let zero = self.d.byte(0);
        let set = self.d.select(cond, &m, &zero);
        self.r.p = self.d.or(&cleared, &set);
    }

    /// Whether any bit of `mask` is set in `v`.
    fn test(&mut self, v: &D::Byte, mask: u8) -> D::Bit {
        let m = self.d.byte(mask);
        let masked = self.d.and(v, &m);
        let zero = self.d.byte(0);
        let is_zero = self.d.eq(&masked, &zero);
        self.d.bit_not(&is_zero)
    }

    fn flag(&mut self, mask: u8) -> D::Bit {
        let p = self.r.p.clone();
        self.test(&p, mask)
    }

    /// Copy bits of `mask` from `v` into P.
    fn copy_flags(&mut self, v: &D::Byte, mask: u8) {
        let keep = self.d.byte(!mask);
        let cleared = self.d.and(&self.r.p, &keep);
        let m = self.d.byte(mask);
        let bits = self.d.and(v, &m);
        self.r.p = self.d.or(&cleared, &bits);
    }

    fn set_nz(&mut self, v: &D::Byte) {
        let zero = self.d.byte(0);
        let is_zero = self.d.eq(v, &zero);
        self.set_flag(FLAG_Z, &is_zero);
        self.copy_flags(v, FLAG_N);
    }

    /// The value of P as stored by `PLP` and `RTI`: B is not a stored flag,
    /// and bit 5 always reads as 1.
    fn pulled_p(&mut self, v: &D::Byte) -> D::Byte {
        let keep = self.d.byte(!(FLAG_B | FLAG_U));
        let kept = self.d.and(v, &keep);
        let u = self.d.byte(FLAG_U);
        self.d.or(&kept, &u)
    }

    /// The value of P as pushed by `PHP` and `BRK`: with B and bit 5 set.
    fn pushed_p(&mut self) -> D::Byte {
        let bu = self.d.byte(FLAG_B | FLAG_U);
        self.d.or(&self.r.p, &bu)
    }

    // --- Addressing ---------------------------------------------------------

    /// Perform the addressing cycles of `mode` and return the effective
    /// address. The final access to that address is left to the caller.
    fn address(&mut self, mode: Mode, access: Access) -> Result<u16, Stop> {
        Ok(match mode {
            Mode::ZeroPage => self.fetch8()? as u16,
            Mode::ZeroPageX | Mode::ZeroPageY => {
                let base = self.fetch8()?;
                self.read(base as u16); // read from the unindexed address
                let index = if mode == Mode::ZeroPageX {
                    self.r.x.clone()
                } else {
                    self.r.y.clone()
                };
                base.wrapping_add(self.c8(&index)?) as u16
            }
            Mode::Absolute => {
                let lo = self.fetch();
                let hi = self.fetch();
                self.word(&lo, &hi)?
            }
            Mode::AbsoluteX | Mode::AbsoluteY => {
                let lo = self.fetch();
                let hi = self.fetch();
                let base = self.word(&lo, &hi)?;
                let index = if mode == Mode::AbsoluteX {
                    self.r.x.clone()
                } else {
                    self.r.y.clone()
                };
                let ea = base.wrapping_add(self.c8(&index)? as u16);
                self.indexed_dummy_read(base, ea, access);
                ea
            }
            Mode::IndirectX => {
                let ptr = self.fetch8()?;
                self.read(ptr as u16); // read from the unindexed pointer
                let p = ptr.wrapping_add(self.c8(&self.r.x.clone())?);
                let lo = self.read(p as u16);
                let hi = self.read(p.wrapping_add(1) as u16);
                self.word(&lo, &hi)?
            }
            Mode::IndirectY => {
                let ptr = self.fetch8()?;
                let lo = self.read(ptr as u16);
                let hi = self.read(ptr.wrapping_add(1) as u16);
                let base = self.word(&lo, &hi)?;
                let ea = base.wrapping_add(self.c8(&self.r.y.clone())? as u16);
                self.indexed_dummy_read(base, ea, access);
                ea
            }
            Mode::Implied | Mode::Accumulator | Mode::Immediate | Mode::Indirect | Mode::Relative => {
                unreachable!("{mode:?} has no effective address")
            }
        })
    }

    /// Indexed modes add the index to the low byte first and read from that
    /// address before the high byte is corrected. The read is a dummy if the
    /// page was crossed. Reads that do not cross a page skip it; writes and
    /// read-modify-writes always take it. See `CPU-PAGE-1`.
    fn indexed_dummy_read(&mut self, base: u16, ea: u16, access: Access) {
        let uncorrected = (base & 0xFF00) | (ea & 0x00FF);
        if access != Access::Read || uncorrected != ea {
            self.read(uncorrected);
        }
    }

    /// The operand of a read instruction: immediate, or read from the
    /// effective address.
    fn operand(&mut self, mode: Mode) -> Result<D::Byte, Stop> {
        if mode == Mode::Immediate {
            return Ok(self.fetch());
        }
        let addr = self.address(mode, Access::Read)?;
        Ok(self.read(addr))
    }

    // --- Instructions -------------------------------------------------------

    fn run(&mut self, op: Op, mode: Mode) -> Result<(), Stop> {
        use Op::*;
        match op {
            Lda | Ldx | Ldy => {
                let v = self.operand(mode)?;
                self.set_nz(&v);
                match op {
                    Lda => self.r.a = v,
                    Ldx => self.r.x = v,
                    _ => self.r.y = v,
                }
            }
            Lxa => {
                // Real chips OR A with an unstable "magic" constant before the
                // AND; $EE is the value Stella uses (64doc.txt), so the two
                // agree. With imm = 0, as in CLEAN_START, the result is 0 on
                // every chip. Two cycles: opcode and immediate fetch.
                let m = self.operand(mode)?;
                let magic = self.d.byte(0xEE);
                let a = self.r.a.clone();
                let ored = self.d.or(&a, &magic);
                let v = self.d.and(&ored, &m);
                self.set_nz(&v);
                self.r.a = v.clone();
                self.r.x = v;
            }
            And | Ora | Eor => {
                let m = self.operand(mode)?;
                let a = self.r.a.clone();
                let v = match op {
                    And => self.d.and(&a, &m),
                    Ora => self.d.or(&a, &m),
                    _ => self.d.xor(&a, &m),
                };
                self.set_nz(&v);
                self.r.a = v;
            }
            Adc => {
                let m = self.operand(mode)?;
                self.adc(m)?;
            }
            Sbc => {
                let m = self.operand(mode)?;
                self.sbc(m)?;
            }
            Cmp | Cpx | Cpy => {
                let m = self.operand(mode)?;
                let reg = match op {
                    Cmp => self.r.a.clone(),
                    Cpx => self.r.x.clone(),
                    _ => self.r.y.clone(),
                };
                let diff = self.d.sub(&reg, &m);
                let borrow = self.d.ult(&reg, &m);
                let carry = self.d.bit_not(&borrow);
                self.set_flag(FLAG_C, &carry);
                self.set_nz(&diff);
            }
            Bit => {
                let m = self.operand(mode)?;
                let a = self.r.a.clone();
                let both = self.d.and(&a, &m);
                let zero = self.d.byte(0);
                let is_zero = self.d.eq(&both, &zero);
                self.set_flag(FLAG_Z, &is_zero);
                self.copy_flags(&m, FLAG_N | FLAG_V);
            }

            Sta | Stx | Sty => {
                let addr = self.address(mode, Access::Write)?;
                let v = match op {
                    Sta => self.r.a.clone(),
                    Stx => self.r.x.clone(),
                    _ => self.r.y.clone(),
                };
                self.write(addr, v);
            }

            Asl | Lsr | Rol | Ror | Inc | Dec => {
                if mode == Mode::Accumulator {
                    self.dummy_read_pc();
                    let a = self.r.a.clone();
                    self.r.a = self.modify(op, &a);
                } else {
                    // Read-modify-write: the unmodified value is written back
                    // before the modified one. See `CPU-RMW-1`.
                    let addr = self.address(mode, Access::ReadModifyWrite)?;
                    let v = self.read(addr);
                    self.write(addr, v.clone());
                    let m = self.modify(op, &v);
                    self.write(addr, m);
                }
            }

            Bpl | Bmi | Bvc | Bvs | Bcc | Bcs | Bne | Beq => {
                let (mask, when_set) = match op {
                    Bpl => (FLAG_N, false),
                    Bmi => (FLAG_N, true),
                    Bvc => (FLAG_V, false),
                    Bvs => (FLAG_V, true),
                    Bcc => (FLAG_C, false),
                    Bcs => (FLAG_C, true),
                    Bne => (FLAG_Z, false),
                    _ => (FLAG_Z, true),
                };
                let set = self.flag(mask);
                let cond = if when_set { set } else { self.d.bit_not(&set) };
                self.branch(&cond)?;
            }

            Jmp => {
                let lo = self.fetch();
                let hi = self.fetch();
                let target = self.word(&lo, &hi)?;
                if mode == Mode::Absolute {
                    self.r.pc = target;
                } else {
                    // The high byte of the pointer is not incremented, so a
                    // pointer at $xxFF reads its high byte from $xx00. See `CPU-JMP-1`.
                    let lo = self.read(target);
                    let hi = self.read((target & 0xFF00) | (target.wrapping_add(1) & 0x00FF));
                    self.r.pc = self.word(&lo, &hi)?;
                }
            }
            Jsr => {
                let lo = self.fetch();
                let s = self.stack_addr()?;
                self.read(s);
                let [pcl, pch] = self.r.pc.to_le_bytes();
                let pch = self.d.byte(pch);
                self.push(pch)?;
                let pcl = self.d.byte(pcl);
                self.push(pcl)?;
                let pc = self.r.pc;
                let hi = self.read(pc);
                self.r.pc = self.word(&lo, &hi)?;
            }
            Rts => {
                self.dummy_read_pc();
                let s = self.stack_addr()?;
                self.read(s);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let lo = self.read(s);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let hi = self.read(s);
                self.r.pc = self.word(&lo, &hi)?;
                self.dummy_read_pc();
                self.r.pc = self.r.pc.wrapping_add(1);
            }
            Rti => {
                self.dummy_read_pc();
                let s = self.stack_addr()?;
                self.read(s);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let p = self.read(s);
                self.r.p = self.pulled_p(&p);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let lo = self.read(s);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let hi = self.read(s);
                self.r.pc = self.word(&lo, &hi)?;
            }
            Brk => {
                self.fetch(); // the padding byte after BRK is read and skipped
                let [pcl, pch] = self.r.pc.to_le_bytes();
                let pch = self.d.byte(pch);
                self.push(pch)?;
                let pcl = self.d.byte(pcl);
                self.push(pcl)?;
                let p = self.pushed_p();
                self.push(p)?;
                let t = self.d.bit(true);
                self.set_flag(FLAG_I, &t);
                let lo = self.read(0xFFFE);
                let hi = self.read(0xFFFF);
                self.r.pc = self.word(&lo, &hi)?;
            }
            Pha | Php => {
                self.dummy_read_pc();
                let v = if op == Pha { self.r.a.clone() } else { self.pushed_p() };
                self.push(v)?;
            }
            Pla | Plp => {
                self.dummy_read_pc();
                let s = self.stack_addr()?;
                self.read(s);
                self.inc_s()?;
                let s = self.stack_addr()?;
                let v = self.read(s);
                if op == Pla {
                    self.set_nz(&v);
                    self.r.a = v;
                } else {
                    self.r.p = self.pulled_p(&v);
                }
            }

            Clc | Sec | Cli | Sei | Cld | Sed | Clv => {
                self.dummy_read_pc();
                let (mask, value) = match op {
                    Clc => (FLAG_C, false),
                    Sec => (FLAG_C, true),
                    Cli => (FLAG_I, false),
                    Sei => (FLAG_I, true),
                    Cld => (FLAG_D, false),
                    Sed => (FLAG_D, true),
                    _ => (FLAG_V, false),
                };
                let b = self.d.bit(value);
                self.set_flag(mask, &b);
            }
            Tax | Tay | Txa | Tya | Tsx => {
                self.dummy_read_pc();
                let v = match op {
                    Tax | Tay => self.r.a.clone(),
                    Txa => self.r.x.clone(),
                    Tya => self.r.y.clone(),
                    _ => self.r.s.clone(),
                };
                self.set_nz(&v);
                match op {
                    Tax | Tsx => self.r.x = v,
                    Tay => self.r.y = v,
                    _ => self.r.a = v,
                }
            }
            Txs => {
                self.dummy_read_pc();
                self.r.s = self.r.x.clone();
            }
            Inx | Iny | Dex | Dey => {
                self.dummy_read_pc();
                let one = self.d.byte(1);
                let reg = if matches!(op, Inx | Dex) {
                    self.r.x.clone()
                } else {
                    self.r.y.clone()
                };
                let v = if matches!(op, Inx | Iny) {
                    self.d.add(&reg, &one)
                } else {
                    self.d.sub(&reg, &one)
                };
                self.set_nz(&v);
                if matches!(op, Inx | Dex) {
                    self.r.x = v;
                } else {
                    self.r.y = v;
                }
            }
            Nop => self.dummy_read_pc(),
        }
        Ok(())
    }

    /// The modify step of shifts, rotates, increments, and decrements.
    fn modify(&mut self, op: Op, v: &D::Byte) -> D::Byte {
        let result = match op {
            Op::Asl | Op::Rol => {
                let carry_in = if op == Op::Rol {
                    Some(self.carry_byte(0x01))
                } else {
                    None
                };
                let out = self.test(v, 0x80);
                let shifted = self.d.shl(v);
                self.set_flag(FLAG_C, &out);
                match carry_in {
                    Some(c) => self.d.or(&shifted, &c),
                    None => shifted,
                }
            }
            Op::Lsr | Op::Ror => {
                let carry_in = if op == Op::Ror {
                    Some(self.carry_byte(0x80))
                } else {
                    None
                };
                let out = self.test(v, 0x01);
                let shifted = self.d.shr(v);
                self.set_flag(FLAG_C, &out);
                match carry_in {
                    Some(c) => self.d.or(&shifted, &c),
                    None => shifted,
                }
            }
            Op::Inc => {
                let one = self.d.byte(1);
                self.d.add(v, &one)
            }
            _ => {
                let one = self.d.byte(1);
                self.d.sub(v, &one)
            }
        };
        self.set_nz(&result);
        result
    }

    /// `bit` if the carry flag is set, else 0.
    fn carry_byte(&mut self, bit: u8) -> D::Byte {
        let c = self.flag(FLAG_C);
        let b = self.d.byte(bit);
        let zero = self.d.byte(0);
        self.d.select(&c, &b, &zero)
    }

    /// Taken branches read the next opcode's address and discard it, and if
    /// the target is on another page, read once more from the target address
    /// with the old high byte. See `CPU-BRANCH-1`.
    fn branch(&mut self, cond: &D::Bit) -> Result<(), Stop> {
        let offset = self.fetch8()? as i8;
        match self.d.to_bool(cond) {
            None => Err(Stop::SymbolicBranch { pc: self.pc0 }),
            Some(false) => Ok(()),
            Some(true) => {
                self.dummy_read_pc();
                let pc = self.r.pc;
                let target = pc.wrapping_add(offset as u16);
                let uncorrected = (pc & 0xFF00) | (target & 0x00FF);
                if uncorrected != target {
                    self.read(uncorrected);
                }
                self.r.pc = target;
                Ok(())
            }
        }
    }

    // --- Arithmetic ---------------------------------------------------------

    fn decimal(&mut self) -> Result<bool, Stop> {
        let d = self.flag(FLAG_D);
        self.d.to_bool(&d).ok_or(Stop::SymbolicDecimal { pc: self.pc0 })
    }

    fn adc(&mut self, m: D::Byte) -> Result<(), Stop> {
        if self.decimal()? {
            return self.adc_decimal(&m);
        }
        self.adc_binary(&m);
        Ok(())
    }

    fn sbc(&mut self, m: D::Byte) -> Result<(), Stop> {
        if self.decimal()? {
            return self.sbc_decimal(&m);
        }
        // A - M - (1 - C) = A + !M + C, with the same flags.
        let not_m = self.d.not(&m);
        self.adc_binary(&not_m);
        Ok(())
    }

    fn adc_binary(&mut self, m: &D::Byte) {
        let a = self.r.a.clone();
        let c = self.carry_byte(0x01);
        let partial = self.d.add(&a, m);
        let carry1 = self.d.ult(&partial, &a);
        let sum = self.d.add(&partial, &c);
        let carry2 = self.d.ult(&sum, &partial);
        let carry = self.d.bit_or(&carry1, &carry2);
        // Overflow: both operands have the same sign and the sum's differs.
        let x1 = self.d.xor(&a, &sum);
        let x2 = self.d.xor(m, &sum);
        let both = self.d.and(&x1, &x2);
        let overflow = self.test(&both, 0x80);
        self.set_flag(FLAG_C, &carry);
        self.set_flag(FLAG_V, &overflow);
        self.set_nz(&sum);
        self.r.a = sum;
    }

    /// Decimal-mode values: A, the operand, and P must all be concrete.
    fn decimal_operands(&self, m: &D::Byte) -> Result<(u8, u8, u8), Stop> {
        let stop = Stop::SymbolicDecimal { pc: self.pc0 };
        let a = self.d.concrete(&self.r.a).ok_or(stop.clone())?;
        let m = self.d.concrete(m).ok_or(stop.clone())?;
        let p = self.d.concrete(&self.r.p).ok_or(stop)?;
        Ok((a, m, p))
    }

    /// NMOS decimal-mode ADC. Z is set from the binary sum; N and V from an
    /// intermediate result; C and A from the decimal sum. See `CPU-DECIMAL-1`.
    fn adc_decimal(&mut self, m: &D::Byte) -> Result<(), Stop> {
        let (a, m, p) = self.decimal_operands(m)?;
        let c = (p & FLAG_C) as i16;
        let binary = (a as u16 + m as u16 + c as u16) as u8;

        let mut lo = (a & 0x0F) as i16 + (m & 0x0F) as i16 + c;
        if lo >= 0x0A {
            lo = ((lo + 0x06) & 0x0F) + 0x10;
        }
        // N and V use the high nibbles as signed values.
        let signed = (a & 0xF0) as i8 as i16 + (m & 0xF0) as i8 as i16 + lo;
        let mut sum = (a & 0xF0) as i16 + (m & 0xF0) as i16 + lo;
        if sum >= 0xA0 {
            sum += 0x60;
        }

        let mut flags = p & !(FLAG_N | FLAG_V | FLAG_Z | FLAG_C);
        if signed & 0x80 != 0 {
            flags |= FLAG_N;
        }
        if !(-128..=127).contains(&signed) {
            flags |= FLAG_V;
        }
        if binary == 0 {
            flags |= FLAG_Z;
        }
        if sum >= 0x100 {
            flags |= FLAG_C;
        }
        self.r.a = self.d.byte(sum as u8);
        self.r.p = self.d.byte(flags);
        Ok(())
    }

    /// NMOS decimal-mode SBC. All flags are set as for binary subtraction;
    /// only A differs. See `CPU-DECIMAL-1`.
    fn sbc_decimal(&mut self, m: &D::Byte) -> Result<(), Stop> {
        let (a, mv, p) = self.decimal_operands(m)?;
        let c = (p & FLAG_C) as i16;

        let mut lo = (a & 0x0F) as i16 - (mv & 0x0F) as i16 + c - 1;
        if lo < 0 {
            lo = ((lo - 0x06) & 0x0F) - 0x10;
        }
        let mut diff = (a & 0xF0) as i16 - (mv & 0xF0) as i16 + lo;
        if diff < 0 {
            diff -= 0x60;
        }

        let not_m = self.d.byte(!mv);
        self.adc_binary(&not_m);
        self.r.a = self.d.byte(diff as u8);
        Ok(())
    }
}
