 processor 6502
 org $F000

 include "vcs.h"
 include "macro.h"

main_loop
     lda #2
     sta VSYNC
     sta VBLANK
     ldx #3
vsync_loop
     sta WSYNC
     dex
     bne vsync_loop

     lda #0
     sta VSYNC
     ldx #37
vblank_loop
     sta WSYNC
     dex
     bne vblank_loop

     lda #0
     sta VBLANK
     lda #$42
     sta COLUBK
     ldx #192
visible_loop
     sta WSYNC
     dex
     bne visible_loop

     ldx #30
overscan_loop
     sta WSYNC
     dex
     bne overscan_loop

     jmp main_loop

 org $FFFA
 .word 0
 .word main_loop
 .word 0
