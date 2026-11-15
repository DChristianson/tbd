//! The symbolic domain (decision 0001): instruction semantics build terms
//! instead of computing values, and an SMT solver answers questions about them.
//!
//! Every term records which of its bits are already known. When all eight are
//! known the term is folded to a constant, so a flag the code has decided
//! stays concrete even when other bits of P are symbolic.
//!
//! The solver runs as a separate process and is spoken to in SMT-LIB, so any
//! SMT-LIB solver can be used. It defaults to `z3`; set `RACER_SMT_SOLVER` to
//! the command for another (for example `cvc5 --lang smt2 --incremental`).
//! Each thread keeps one solver process running, and separates its queries
//! with `push` and `pop` (decision 0001, follow-up 1). Set
//! `RACER_SMT_PERSISTENT=0` to start a process for every query instead.

use crate::domain::Domain;
use std::cell::RefCell;
use std::collections::HashMap;
use std::fmt::Write as _;
use std::io::{self, BufRead, BufReader, Write as _};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::rc::Rc;

pub type Byte = Rc<Node>;
pub type Bit = Rc<BitNode>;

#[derive(Debug)]
pub enum Op {
    Const(u8),
    Var(Rc<str>),
    Add(Byte, Byte),
    Sub(Byte, Byte),
    And(Byte, Byte),
    Or(Byte, Byte),
    Xor(Byte, Byte),
    Not(Byte),
    Shl(Byte),
    Shr(Byte),
    Select(Bit, Byte, Byte),
}

/// A byte-valued term, with the bits already known about it.
#[derive(Debug)]
pub struct Node {
    pub op: Op,
    /// Bits whose value is known.
    pub known: u8,
    /// The value of the known bits.
    pub value: u8,
}

#[derive(Debug)]
pub enum BitNode {
    Const(bool),
    Eq(Byte, Byte),
    Ult(Byte, Byte),
    And(Bit, Bit),
    Or(Bit, Bit),
    Not(Bit),
}

fn node(op: Op, known: u8, value: u8) -> Byte {
    if known == 0xFF {
        constant(value)
    } else {
        Rc::new(Node {
            op,
            known,
            value: value & known,
        })
    }
}

pub fn constant(v: u8) -> Byte {
    Rc::new(Node {
        op: Op::Const(v),
        known: 0xFF,
        value: v,
    })
}

pub fn var(name: &str) -> Byte {
    Rc::new(Node {
        op: Op::Var(name.into()),
        known: 0,
        value: 0,
    })
}

/// A byte whose `known` bits are fixed to `value` and whose other bits come
/// from the variable `name`.
pub fn partly_known(name: &str, known: u8, value: u8) -> Byte {
    let mut d = Symbolic;
    let v = var(name);
    let keep = constant(!known);
    let fixed = constant(value & known);
    let unknown = d.and(&v, &keep);
    d.or(&unknown, &fixed)
}

fn as_const(b: &Byte) -> Option<u8> {
    (b.known == 0xFF).then_some(b.value)
}

fn bit_const(v: bool) -> Bit {
    Rc::new(BitNode::Const(v))
}

fn as_bit_const(b: &Bit) -> Option<bool> {
    match **b {
        BitNode::Const(v) => Some(v),
        _ => None,
    }
}

/// The symbolic domain. It holds no state: terms are reference-counted.
#[derive(Clone, Copy, Debug, Default)]
pub struct Symbolic;

impl Domain for Symbolic {
    type Byte = Byte;
    type Bit = Bit;

    fn byte(&mut self, v: u8) -> Byte {
        constant(v)
    }

    fn concrete(&self, b: &Byte) -> Option<u8> {
        as_const(b)
    }

    fn add(&mut self, a: &Byte, b: &Byte) -> Byte {
        match (as_const(a), as_const(b)) {
            (Some(x), Some(y)) => constant(x.wrapping_add(y)),
            (Some(0), _) => b.clone(),
            (_, Some(0)) => a.clone(),
            _ => node(Op::Add(a.clone(), b.clone()), 0, 0),
        }
    }

    fn sub(&mut self, a: &Byte, b: &Byte) -> Byte {
        match (as_const(a), as_const(b)) {
            (Some(x), Some(y)) => constant(x.wrapping_sub(y)),
            (_, Some(0)) => a.clone(),
            _ => node(Op::Sub(a.clone(), b.clone()), 0, 0),
        }
    }

    fn and(&mut self, a: &Byte, b: &Byte) -> Byte {
        // A bit is known 0 if it is known 0 in either; known 1 if known 1 in both.
        if a.known & a.value == 0xFF {
            return b.clone();
        }
        if b.known & b.value == 0xFF {
            return a.clone();
        }
        let zero = (a.known & !a.value) | (b.known & !b.value);
        let one = (a.known & a.value) & (b.known & b.value);
        node(Op::And(a.clone(), b.clone()), zero | one, one)
    }

    fn or(&mut self, a: &Byte, b: &Byte) -> Byte {
        // A bit is known 1 if it is known 1 in either; known 0 if known 0 in both.
        if a.known & !a.value == 0xFF {
            return b.clone();
        }
        if b.known & !b.value == 0xFF {
            return a.clone();
        }
        let one = (a.known & a.value) | (b.known & b.value);
        let zero = (a.known & !a.value) & (b.known & !b.value);
        node(Op::Or(a.clone(), b.clone()), zero | one, one)
    }

    fn xor(&mut self, a: &Byte, b: &Byte) -> Byte {
        if a.known & !a.value == 0xFF {
            return b.clone();
        }
        if b.known & !b.value == 0xFF {
            return a.clone();
        }
        node(Op::Xor(a.clone(), b.clone()), a.known & b.known, a.value ^ b.value)
    }

    fn not(&mut self, a: &Byte) -> Byte {
        node(Op::Not(a.clone()), a.known, !a.value)
    }

    fn shl(&mut self, a: &Byte) -> Byte {
        node(Op::Shl(a.clone()), (a.known << 1) | 0x01, a.value << 1)
    }

    fn shr(&mut self, a: &Byte) -> Byte {
        node(Op::Shr(a.clone()), (a.known >> 1) | 0x80, a.value >> 1)
    }

    fn eq(&mut self, a: &Byte, b: &Byte) -> Bit {
        let both = a.known & b.known;
        if (a.value ^ b.value) & both != 0 {
            bit_const(false)
        } else if both == 0xFF || Rc::ptr_eq(a, b) {
            bit_const(true)
        } else {
            Rc::new(BitNode::Eq(a.clone(), b.clone()))
        }
    }

    fn ult(&mut self, a: &Byte, b: &Byte) -> Bit {
        match (as_const(a), as_const(b)) {
            (Some(x), Some(y)) => bit_const(x < y),
            (_, Some(0)) => bit_const(false),
            _ if Rc::ptr_eq(a, b) => bit_const(false),
            _ => Rc::new(BitNode::Ult(a.clone(), b.clone())),
        }
    }

    fn select(&mut self, c: &Bit, t: &Byte, e: &Byte) -> Byte {
        match as_bit_const(c) {
            Some(true) => t.clone(),
            Some(false) => e.clone(),
            None if Rc::ptr_eq(t, e) => t.clone(),
            None => {
                let agree = t.known & e.known & !(t.value ^ e.value);
                node(Op::Select(c.clone(), t.clone(), e.clone()), agree, t.value)
            }
        }
    }

    fn bit(&mut self, v: bool) -> Bit {
        bit_const(v)
    }

    fn bit_and(&mut self, a: &Bit, b: &Bit) -> Bit {
        match (as_bit_const(a), as_bit_const(b)) {
            (Some(false), _) | (_, Some(false)) => bit_const(false),
            (Some(true), _) => b.clone(),
            (_, Some(true)) => a.clone(),
            _ => Rc::new(BitNode::And(a.clone(), b.clone())),
        }
    }

    fn bit_or(&mut self, a: &Bit, b: &Bit) -> Bit {
        match (as_bit_const(a), as_bit_const(b)) {
            (Some(true), _) | (_, Some(true)) => bit_const(true),
            (Some(false), _) => b.clone(),
            (_, Some(false)) => a.clone(),
            _ => Rc::new(BitNode::Or(a.clone(), b.clone())),
        }
    }

    fn bit_not(&mut self, a: &Bit) -> Bit {
        match as_bit_const(a) {
            Some(v) => bit_const(!v),
            None => Rc::new(BitNode::Not(a.clone())),
        }
    }

    fn to_bool(&self, b: &Bit) -> Option<bool> {
        as_bit_const(b)
    }
}

// ---------------------------------------------------------------------------
// Evaluation under an assignment, used to check the symbolic domain against
// the concrete one.

pub type Assignment = HashMap<String, u8>;

pub fn eval(b: &Byte, env: &Assignment) -> u8 {
    match &b.op {
        Op::Const(v) => *v,
        Op::Var(name) => env.get(&**name).copied().unwrap_or(0),
        Op::Add(x, y) => eval(x, env).wrapping_add(eval(y, env)),
        Op::Sub(x, y) => eval(x, env).wrapping_sub(eval(y, env)),
        Op::And(x, y) => eval(x, env) & eval(y, env),
        Op::Or(x, y) => eval(x, env) | eval(y, env),
        Op::Xor(x, y) => eval(x, env) ^ eval(y, env),
        Op::Not(x) => !eval(x, env),
        Op::Shl(x) => eval(x, env) << 1,
        Op::Shr(x) => eval(x, env) >> 1,
        Op::Select(c, t, e) => {
            if eval_bit(c, env) {
                eval(t, env)
            } else {
                eval(e, env)
            }
        }
    }
}

pub fn eval_bit(b: &Bit, env: &Assignment) -> bool {
    match &**b {
        BitNode::Const(v) => *v,
        BitNode::Eq(x, y) => eval(x, env) == eval(y, env),
        BitNode::Ult(x, y) => eval(x, env) < eval(y, env),
        BitNode::And(x, y) => eval_bit(x, env) && eval_bit(y, env),
        BitNode::Or(x, y) => eval_bit(x, env) || eval_bit(y, env),
        BitNode::Not(x) => !eval_bit(x, env),
    }
}

// ---------------------------------------------------------------------------
// SMT-LIB output. Shared subterms are emitted once, as definitions.

struct Printer {
    out: String,
    names: HashMap<usize, String>,
    vars: Vec<String>,
}

impl Printer {
    fn byte(&mut self, b: &Byte) -> String {
        let key = Rc::as_ptr(b) as usize;
        if let Some(name) = self.names.get(&key) {
            return name.clone();
        }
        let expr = match &b.op {
            Op::Const(v) => return format!("#x{v:02x}"),
            Op::Var(name) => {
                if !self.vars.iter().any(|v| v == &**name) {
                    self.vars.push(name.to_string());
                }
                return name.to_string();
            }
            Op::Add(x, y) => format!("(bvadd {} {})", self.byte(x), self.byte(y)),
            Op::Sub(x, y) => format!("(bvsub {} {})", self.byte(x), self.byte(y)),
            Op::And(x, y) => format!("(bvand {} {})", self.byte(x), self.byte(y)),
            Op::Or(x, y) => format!("(bvor {} {})", self.byte(x), self.byte(y)),
            Op::Xor(x, y) => format!("(bvxor {} {})", self.byte(x), self.byte(y)),
            Op::Not(x) => format!("(bvnot {})", self.byte(x)),
            Op::Shl(x) => format!("(bvshl {} #x01)", self.byte(x)),
            Op::Shr(x) => format!("(bvlshr {} #x01)", self.byte(x)),
            Op::Select(c, t, e) => format!("(ite {} {} {})", self.bit(c), self.byte(t), self.byte(e)),
        };
        let name = format!("t{}", self.names.len());
        let _ = writeln!(self.out, "(define-fun {name} () (_ BitVec 8) {expr})");
        self.names.insert(key, name.clone());
        name
    }

    fn bit(&mut self, b: &Bit) -> String {
        match &**b {
            BitNode::Const(v) => v.to_string(),
            BitNode::Eq(x, y) => format!("(= {} {})", self.byte(x), self.byte(y)),
            BitNode::Ult(x, y) => format!("(bvult {} {})", self.byte(x), self.byte(y)),
            BitNode::And(x, y) => format!("(and {} {})", self.bit(x), self.bit(y)),
            BitNode::Or(x, y) => format!("(or {} {})", self.bit(x), self.bit(y)),
            BitNode::Not(x) => format!("(not {})", self.bit(x)),
        }
    }
}

/// The declarations, definitions, and assertion for `constraint`, without
/// a logic or a command, and the variable names.
fn query(constraint: &Bit) -> (String, Vec<String>) {
    let mut p = Printer {
        out: String::new(),
        names: HashMap::new(),
        vars: Vec::new(),
    };
    let assertion = p.bit(constraint);
    let mut script = String::new();
    for v in &p.vars {
        let _ = writeln!(script, "(declare-const {v} (_ BitVec 8))");
    }
    script.push_str(&p.out);
    let _ = writeln!(script, "(assert {assertion})");
    (script, p.vars)
}

/// Render an SMT-LIB script asserting `constraint`, with its variables
/// declared. Returns the script and the variable names.
pub fn smtlib(constraint: &Bit) -> (String, Vec<String>) {
    let (body, vars) = query(constraint);
    let mut script = format!("(set-logic QF_BV)\n{body}(check-sat)\n");
    if !vars.is_empty() {
        let _ = writeln!(script, "(get-value ({}))", vars.join(" "));
    }
    (script, vars)
}

fn solver_command() -> Command {
    let command = std::env::var("RACER_SMT_SOLVER").unwrap_or_else(|_| "z3 -in -smt2".into());
    let mut parts = command.split_whitespace();
    let mut c = Command::new(parts.next().unwrap_or("z3"));
    c.args(parts).stdin(Stdio::piped()).stdout(Stdio::piped());
    c
}

fn persistent() -> bool {
    std::env::var("RACER_SMT_PERSISTENT").map_or(true, |v| v != "0")
}

/// One solver process, kept running between queries.
struct Session {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

/// Printed after each command's output, so the end of a reply is found
/// without parsing it.
const END: &str = "racer-end";

impl Session {
    fn start() -> io::Result<Session> {
        let mut child = solver_command().spawn()?;
        let mut input = child.stdin.take().expect("solver stdin");
        let output = BufReader::new(child.stdout.take().expect("solver stdout"));
        input.write_all(b"(set-option :print-success false)\n(set-logic QF_BV)\n")?;
        Ok(Session { child, input, output })
    }

    /// Send `commands`, and return what the solver printed for them.
    fn send(&mut self, commands: &str) -> io::Result<String> {
        writeln!(self.input, "{commands}(echo \"{END}\")")?;
        self.input.flush()?;
        let mut reply = String::new();
        loop {
            let mut line = String::new();
            if self.output.read_line(&mut line)? == 0 {
                return Err(io::Error::other(format!("the solver exited\n{reply}")));
            }
            if line.trim().trim_matches('"') == END {
                return Ok(reply);
            }
            reply.push_str(&line);
        }
    }

    /// Check `body`, and, if satisfiable, get the values of `vars`.
    fn check(&mut self, body: &str, vars: &[String]) -> io::Result<(String, String)> {
        let verdict = self.send(&format!("(push 1)\n{body}(check-sat)\n"))?;
        let values = if verdict.trim() == "sat" && !vars.is_empty() {
            self.send(&format!("(get-value ({}))\n", vars.join(" ")))?
        } else {
            String::new()
        };
        writeln!(self.input, "(pop 1)")?;
        Ok((verdict, values))
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

thread_local! {
    static SESSION: RefCell<Option<Session>> = const { RefCell::new(None) };
}

/// Run one query, in this thread's solver process, started if need be. A
/// process that fails is dropped, so the next query starts another.
fn run_persistent(body: &str, vars: &[String]) -> io::Result<(String, String)> {
    SESSION.with(|cell| {
        let mut slot = cell.borrow_mut();
        if slot.is_none() {
            *slot = Some(Session::start()?);
        }
        let result = slot.as_mut().expect("started").check(body, vars);
        if result.is_err() {
            *slot = None;
        }
        result
    })
}

/// Run one query in a process of its own.
fn run_once(constraint: &Bit) -> io::Result<(String, String)> {
    let (script, _) = smtlib(constraint);
    let mut child = solver_command().spawn()?;
    child.stdin.take().expect("solver stdin").write_all(script.as_bytes())?;
    let output = child.wait_with_output()?;
    let out = String::from_utf8_lossy(&output.stdout).into_owned();
    let (verdict, values) = out.split_once('\n').unwrap_or((&out, ""));
    Ok((verdict.to_string(), values.to_string()))
}

/// Find an assignment satisfying `constraint`, or `None` if there is none.
pub fn solve(constraint: &Bit) -> io::Result<Option<Assignment>> {
    if let Some(v) = as_bit_const(constraint) {
        return Ok(v.then(Assignment::new));
    }
    let (body, vars) = query(constraint);
    let (verdict, values) = if persistent() {
        run_persistent(&body, &vars)?
    } else {
        run_once(constraint)?
    };
    match verdict.trim() {
        "unsat" => Ok(None),
        "sat" => {
            let mut model = Assignment::new();
            for v in vars {
                // The solver prints each value as `(name #xHH)`.
                let pattern = format!("({v} #x");
                if let Some(i) = values.find(&pattern) {
                    let start = i + pattern.len();
                    let hex = values.get(start..start + 2).unwrap_or("00");
                    model.insert(v, u8::from_str_radix(hex, 16).unwrap_or(0));
                }
            }
            Ok(Some(model))
        }
        other => Err(io::Error::other(format!(
            "unexpected solver output: {other:?}\n{values}"
        ))),
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Proof {
    /// The property holds for every assignment.
    Proved,
    /// An assignment for which it fails.
    Counterexample(Assignment),
}

/// Check that `property` holds for every assignment of its variables.
pub fn prove(property: &Bit) -> io::Result<Proof> {
    let mut d = Symbolic;
    Ok(match solve(&d.bit_not(property))? {
        None => Proof::Proved,
        Some(cex) => Proof::Counterexample(cex),
    })
}
