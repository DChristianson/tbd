 processor 6502
 include "vcs.h"
 include "macro.h"

 org $F000

Start:
   CLEAN_START
   lda #$42
   sta COLUBK

MainLoop:
   ; VSYNC: 3 lines
   ldx #3
VsyncLoop:
   lda #2
   sta VSYNC
   sta WSYNC
   dex
   bne VsyncLoop

   ; Turn off VSYNC
   lda #0
   sta VSYNC

   ; VBLANK: 37 lines
   ldx #37
VblankLoop:
   lda #2
   sta VBLANK
   sta WSYNC
   dex
   bne VblankLoop

   ; Turn off VBLANK
   lda #0
   sta VBLANK

   ; Visible: 190 lines
   ldx #190
VisibleLoop:
   sta WSYNC
   dex
   bne VisibleLoop

   ; Overscan: 32 lines
   ldx #32
OverscanLoop:
   lda #2
   sta VBLANK
   sta WSYNC
   dex
   bne OverscanLoop

   ; Turn off VBLANK
   lda #0
   sta VBLANK

   jmp MainLoop

 org $FFFA
   .word Start
   .word Start
   .word Start
