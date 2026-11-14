//! Value domains (decision 0001). Instruction semantics in `cpu.rs` are
//! written once against this trait and run either concretely (`Concrete`,
//! plain bytes) or symbolically (`symbolic::Symbolic`, terms for an SMT
//! solver).
//!
//! Addresses are always concrete. Where an address is computed from a value
//! (an index register, a pointer read from memory), the CPU asks the domain
//! for the value's concrete byte and stops if it has none.

pub trait Domain {
    /// An 8-bit value: a register, the status byte, or a memory byte.
    type Byte: Clone;
    /// One bit of truth: the result of a comparison or a flag test.
    type Bit: Clone;

    fn byte(&mut self, v: u8) -> Self::Byte;
    /// The byte's value, if it is known without a solver.
    fn concrete(&self, b: &Self::Byte) -> Option<u8>;

    /// Addition modulo 256.
    fn add(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Byte;
    /// Subtraction modulo 256.
    fn sub(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Byte;
    fn and(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Byte;
    fn or(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Byte;
    fn xor(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Byte;
    fn not(&mut self, a: &Self::Byte) -> Self::Byte;
    /// Shift left by one bit; bit 0 becomes 0.
    fn shl(&mut self, a: &Self::Byte) -> Self::Byte;
    /// Shift right by one bit; bit 7 becomes 0.
    fn shr(&mut self, a: &Self::Byte) -> Self::Byte;

    fn eq(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Bit;
    /// Unsigned less-than.
    fn ult(&mut self, a: &Self::Byte, b: &Self::Byte) -> Self::Bit;
    fn select(&mut self, c: &Self::Bit, t: &Self::Byte, e: &Self::Byte) -> Self::Byte;

    fn bit(&mut self, v: bool) -> Self::Bit;
    fn bit_and(&mut self, a: &Self::Bit, b: &Self::Bit) -> Self::Bit;
    fn bit_or(&mut self, a: &Self::Bit, b: &Self::Bit) -> Self::Bit;
    fn bit_not(&mut self, a: &Self::Bit) -> Self::Bit;
    /// The bit's value, if it is known without a solver.
    fn to_bool(&self, b: &Self::Bit) -> Option<bool>;
}

/// Plain bytes: the domain the simulator runs in.
#[derive(Clone, Copy, Debug, Default)]
pub struct Concrete;

impl Domain for Concrete {
    type Byte = u8;
    type Bit = bool;

    #[inline]
    fn byte(&mut self, v: u8) -> u8 {
        v
    }
    #[inline]
    fn concrete(&self, b: &u8) -> Option<u8> {
        Some(*b)
    }
    #[inline]
    fn add(&mut self, a: &u8, b: &u8) -> u8 {
        a.wrapping_add(*b)
    }
    #[inline]
    fn sub(&mut self, a: &u8, b: &u8) -> u8 {
        a.wrapping_sub(*b)
    }
    #[inline]
    fn and(&mut self, a: &u8, b: &u8) -> u8 {
        a & b
    }
    #[inline]
    fn or(&mut self, a: &u8, b: &u8) -> u8 {
        a | b
    }
    #[inline]
    fn xor(&mut self, a: &u8, b: &u8) -> u8 {
        a ^ b
    }
    #[inline]
    fn not(&mut self, a: &u8) -> u8 {
        !a
    }
    #[inline]
    fn shl(&mut self, a: &u8) -> u8 {
        a << 1
    }
    #[inline]
    fn shr(&mut self, a: &u8) -> u8 {
        a >> 1
    }
    #[inline]
    fn eq(&mut self, a: &u8, b: &u8) -> bool {
        a == b
    }
    #[inline]
    fn ult(&mut self, a: &u8, b: &u8) -> bool {
        a < b
    }
    #[inline]
    fn select(&mut self, c: &bool, t: &u8, e: &u8) -> u8 {
        if *c {
            *t
        } else {
            *e
        }
    }
    #[inline]
    fn bit(&mut self, v: bool) -> bool {
        v
    }
    #[inline]
    fn bit_and(&mut self, a: &bool, b: &bool) -> bool {
        *a && *b
    }
    #[inline]
    fn bit_or(&mut self, a: &bool, b: &bool) -> bool {
        *a || *b
    }
    #[inline]
    fn bit_not(&mut self, a: &bool) -> bool {
        !*a
    }
    #[inline]
    fn to_bool(&self, b: &bool) -> Option<bool> {
        Some(*b)
    }
}
